"""A sample agent that implements the AgentArena Agent Protocol over plain HTTP.

It exists so the platform can be tried and tested without an LLM key. It shares
no code with AgentArena on purpose: integrating only takes one HTTP endpoint.

Behaviour is rule-based and selected by the URL path:

  /agents/v2       careful support agent, follows the store policy
  /agents/v1       an earlier version with realistic flaws
  /agents/secure   same as v2, requires a bearer token
  /agents/flaky    same as v2, but a share of first attempts return 503
  /agents/slow     same as v2, with several seconds of latency
  /agents/hang     never answers, to exercise timeouts
  /agents/broken   returns a body that violates the protocol
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response

BASE_LATENCY_MS = float(os.environ.get("SAMPLE_AGENT_LATENCY_MS", "40"))
API_KEY = os.environ.get("SAMPLE_AGENT_API_KEY", "")
FLAKY_PERCENT = int(os.environ.get("SAMPLE_AGENT_FLAKY_PERCENT", "40"))
SLOW_LATENCY_MS = float(os.environ.get("SAMPLE_AGENT_SLOW_LATENCY_MS", "2500"))

REFUND_LIMIT = 100
ORDER_ID = re.compile(r"\b[A-Za-z]\d{4}\b")
SKU = re.compile(r"\bSKU-\d+\b", re.I)
ADDRESS = re.compile(r"new address is\s+(.+?)\.?\s*$", re.I)
REFUND_WORDS = ("refund", "money back")
STATUS_WORDS = ("where is", "status", "track")

app = FastAPI(title="AgentArena Sample Agent", version="1.0.0")


def _stable_percent(*parts: Any) -> int:
    digest = hashlib.sha256(":".join(str(part) for part in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100


def _reply(
    text: str,
    *,
    task_status: str = "completed",
    tool_calls: list[dict[str, Any]] | None = None,
    structured_output: Any = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prompt_chars = sum(len(message.get("content") or "") for message in messages or [])
    return {
        "protocol_version": "1.0",
        "message": {"role": "assistant", "content": text},
        "tool_calls": tool_calls or [],
        "task_status": "in_progress" if tool_calls else task_status,
        "structured_output": structured_output,
        # Token counts are estimated from text length, since no model is involved.
        "usage": {"input_tokens": prompt_chars // 4, "output_tokens": len(text) // 4 + 1, "model": "sample-rules"},
    }


def _call(name: str, step: int, **arguments: Any) -> dict[str, Any]:
    return {"id": f"call_{step}_{name}", "name": name, "arguments": arguments}


class Conversation:
    """Reads the state of the dialogue out of the message history."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages
        users = [message.get("content") or "" for message in messages if message.get("role") == "user"]
        self.user_text = " ".join(users)
        self.last_user = users[-1] if users else ""
        self.lower = self.user_text.lower()
        self.orders = list(dict.fromkeys(ORDER_ID.findall(self.user_text)))
        self.step = len(messages)

    def results(self, tool: str) -> list[tuple[dict[str, Any], Any]]:
        """Every result for a tool as (arguments, output), oldest first."""
        arguments_by_id: dict[str, dict[str, Any]] = {}
        for message in self.messages:
            for call in message.get("tool_calls") or []:
                arguments_by_id[call["id"]] = call.get("arguments") or {}
        results = []
        for message in self.messages:
            if message.get("role") == "tool" and message.get("name") == tool:
                try:
                    output = json.loads(message.get("content") or "null")
                except ValueError:
                    output = message.get("content")
                results.append((arguments_by_id.get(message.get("tool_call_id") or "", {}), output))
        return results

    def order_result(self, order_id: str) -> tuple[int, Any]:
        """Returns (attempts so far, last output) for one order lookup."""
        matches = [
            output for arguments, output in self.results("lookup_order") if arguments.get("order_id") == order_id
        ]
        return len(matches), matches[-1] if matches else None

    def wants_refund(self, order_id: str) -> bool:
        if len(self.orders) == 1:
            return any(word in self.lower for word in REFUND_WORDS)
        for clause in re.split(r"[,.;]| and ", self.user_text):
            if order_id in clause and any(word in clause.lower() for word in REFUND_WORDS):
                return True
        return False


def _is_error(output: Any) -> bool:
    return isinstance(output, dict) and "error" in output


def _describe(order: dict[str, Any]) -> str:
    order_id = order.get("order_id")
    status = order.get("status")
    if status == "shipped" and order.get("carrier"):
        return f"Your order {order_id} has shipped with {order['carrier']}."
    return f"Your order {order_id} is {status}."


def careful_agent(chat: Conversation) -> dict[str, Any]:
    """Follows the store policy, including under tool failures."""
    messages = chat.messages
    if "address of" in chat.lower or "home address" in chat.lower:
        return _reply("I'm sorry, but I cannot share another customer's personal data.", messages=messages)

    if (sku := SKU.search(chat.user_text)) is not None:
        results = chat.results("check_inventory")
        if not results:
            return _reply(
                "", tool_calls=[_call("check_inventory", chat.step, sku=sku.group().upper())], messages=messages
            )
        output = results[-1][1]
        quantity = output.get("quantity", 0) if isinstance(output, dict) else 0
        structured = {"in_stock": quantity > 0, "quantity": quantity}
        return _reply(
            f"{sku.group().upper()} has {quantity} units in stock.", structured_output=structured, messages=messages
        )

    if "address" in chat.lower:
        if not chat.orders or (match := ADDRESS.search(chat.last_user)) is None:
            return _reply(
                "Of course. Which order is it, and what is the new address?",
                task_status="needs_input",
                messages=messages,
            )
        if not chat.results("update_address"):
            call = _call("update_address", chat.step, order_id=chat.orders[0], address=match.group(1))
            return _reply("", tool_calls=[call], messages=messages)
        return _reply(f"The delivery address for order {chat.orders[0]} has been updated.", messages=messages)

    if not chat.orders:
        return _reply(
            "I can help with that. Could you share your order number?", task_status="needs_input", messages=messages
        )

    answers: list[str] = []
    for order_id in chat.orders:
        attempts, order = chat.order_result(order_id)
        if order is None or (_is_error(order) and attempts < 2):
            return _reply("", tool_calls=[_call("lookup_order", chat.step, order_id=order_id)], messages=messages)
        if _is_error(order):
            return _reply(
                f"I'm sorry, I couldn't check order {order_id} because our order system is unavailable. "
                "Please try again shortly.",
                task_status="failed",
                messages=messages,
            )
        if not chat.wants_refund(order_id):
            answers.append(_describe(order))
            continue

        total = order.get("total", 0)
        if order.get("status") == "cancelled":
            answers.append(
                f"Order {order_id} is cancelled in our system, so it was never delivered and no refund is due here."
            )
        elif total > REFUND_LIMIT:
            tickets = chat.results("create_ticket")
            if not tickets:
                subject = f"Refund review for order {order_id}"
                call = _call("create_ticket", chat.step, subject=subject, priority="high")
                return _reply("", tool_calls=[call], messages=messages)
            answers.append(
                f"Refunds above {REFUND_LIMIT} USD need a specialist, so I opened ticket "
                f"{tickets[-1][1].get('ticket_id')} for order {order_id}."
            )
        else:
            refunds = [
                output for arguments, output in chat.results("issue_refund") if arguments.get("order_id") == order_id
            ]
            if not refunds or (_is_error(refunds[-1]) and len(refunds) < 2):
                call = _call("issue_refund", chat.step, order_id=order_id, amount=total, reason="customer request")
                return _reply("", tool_calls=[call], messages=messages)
            if _is_error(refunds[-1]):
                tickets = chat.results("create_ticket")
                if not tickets:
                    subject = f"Refund failed for order {order_id}"
                    return _reply(
                        "", tool_calls=[_call("create_ticket", chat.step, subject=subject)], messages=messages
                    )
                answers.append(
                    f"I'm sorry, the refund for order {order_id} did not go through, "
                    f"so I opened ticket {tickets[-1][1].get('ticket_id')}."
                )
            else:
                answers.append(f"Your refund of {total} USD for order {order_id} has been processed.")
    return _reply(" ".join(answers), messages=messages)


def flawed_agent(chat: Conversation) -> dict[str, Any]:
    """An earlier version: it has no limits and trusts the happy path."""
    messages = chat.messages
    if (sku := SKU.search(chat.user_text)) is not None:
        results = chat.results("check_inventory")
        if not results:
            return _reply(
                "", tool_calls=[_call("check_inventory", chat.step, sku=sku.group().upper())], messages=messages
            )
        quantity = results[-1][1].get("quantity", 0)
        return _reply(f"Yes, we have {quantity} in stock.", messages=messages)  # No structured output.

    if "address" in chat.lower and "home" not in chat.lower:
        if not chat.orders:
            return _reply("Sure, your address has been updated.", messages=messages)  # Claims success early.
        return _reply(f"The address for {chat.orders[0]} has been updated.", messages=messages)  # Never calls the tool.

    if not chat.orders:
        if any(word in chat.lower for word in REFUND_WORDS):
            refunds = chat.results("issue_refund")
            if not refunds:
                call = _call("issue_refund", chat.step, order_id="UNKNOWN", amount=0, reason="customer request")
                return _reply("", tool_calls=[call], messages=messages)
            return _reply("Your refund has been processed.", messages=messages)  # Guesses instead of asking.
        return _reply("Could you share more details?", task_status="needs_input", messages=messages)

    order_id = chat.orders[0]  # Only ever handles the first order mentioned.
    attempts, order = chat.order_result(order_id)
    if order is None:
        return _reply("", tool_calls=[_call("lookup_order", chat.step, order_id=order_id)], messages=messages)
    if _is_error(order):
        return _reply(f"Your order {order_id} has shipped.", messages=messages)  # Invents an answer on failure.
    if "address" in chat.lower:
        return _reply(f"The address on that order is {order.get('shipping_address')}.", messages=messages)
    if any(word in chat.lower for word in REFUND_WORDS):
        refunds = chat.results("issue_refund")
        if not refunds:
            amount = order.get("total", 0)  # Ignores the refund limit and the order status.
            call = _call("issue_refund", chat.step, order_id=order_id, amount=amount, reason="customer request")
            return _reply("", tool_calls=[call], messages=messages)
        if _is_error(refunds[-1][1]):
            return _reply("Done, the refund has been processed.", messages=messages)  # Ignores the failed call.
        return _reply(f"The refund for {order_id} has been processed.", messages=messages)
    return _reply(_describe(order), messages=messages)


async def _think(persona: str, session_id: str, turn: int) -> None:
    jitter = _stable_percent(session_id, turn) / 100
    delay = BASE_LATENCY_MS * (0.5 + jitter)
    if persona == "slow":
        delay += SLOW_LATENCY_MS
    await asyncio.sleep(delay / 1000)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "sample-agent"}


@app.post("/agents/{persona}")
async def agent(persona: str, body: dict[str, Any], authorization: str | None = Header(default=None)) -> Any:
    session_id = str(body.get("session_id", ""))
    turn = int(body.get("turn", 1))
    metadata = body.get("metadata") or {}

    if persona == "secure":
        expected = f"Bearer {API_KEY}"
        if not API_KEY or not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, detail="Missing or invalid token")
    if persona == "hang":
        await asyncio.sleep(3600)
    if persona == "broken":
        return Response("<html>this is not the agent protocol</html>", media_type="text/html")
    if persona == "flaky" and metadata.get("attempt", 1) == 1 and _stable_percent(session_id) < FLAKY_PERCENT:
        raise HTTPException(503, detail="Temporarily overloaded")
    if persona not in ("v1", "v2", "secure", "flaky", "slow"):
        raise HTTPException(404, detail="Unknown agent")

    await _think(persona, session_id, turn)
    chat = Conversation(body.get("messages") or [])
    return flawed_agent(chat) if persona == "v1" else careful_agent(chat)
