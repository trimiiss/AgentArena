# AgentArena Agent Protocol v1

Any agent, in any framework, can be benchmarked by exposing one HTTP endpoint. AgentArena never runs your code. It sends a request for each step of a conversation and reads your reply.

The JSON Schemas in this folder are the formal contract: [`request.v1.schema.json`](request.v1.schema.json) and [`response.v1.schema.json`](response.v1.schema.json). They are generated from the code that AgentArena runs.

## Request

AgentArena sends `POST <your endpoint>` with `Content-Type: application/json`.

```json
{
  "protocol_version": "1.0",
  "session_id": "6f1c9c52-1f0a-4c35-b0a1-0d8a3f6a2c11",
  "turn": 1,
  "messages": [
    { "role": "system", "content": "You are the support agent for ACME Store." },
    { "role": "user", "content": "Where is my order A1001?" }
  ],
  "tools": [
    {
      "name": "lookup_order",
      "description": "Fetch an order by id.",
      "parameters": { "type": "object", "properties": { "order_id": { "type": "string" } } }
    }
  ],
  "context": { "store": "ACME Store" },
  "metadata": { "run_id": "...", "seed": 1234, "attempt": 1 }
}
```

`messages` holds the whole conversation so far, so your agent can be stateless. `session_id` stays the same for one execution.

## Response

Return HTTP 200 with a JSON body. Only `message` is required.

```json
{
  "protocol_version": "1.0",
  "message": { "role": "assistant", "content": "Your order A1001 has shipped with DHL." },
  "tool_calls": [],
  "task_status": "completed",
  "structured_output": null,
  "usage": { "input_tokens": 120, "output_tokens": 14, "model": "my-model", "cost_usd": 0.0004 }
}
```

| Field | Meaning |
| --- | --- |
| `message.content` | What the agent says to the user. |
| `tool_calls` | Tools to run. Leave empty when the agent has finished this turn. |
| `task_status` | `completed`, `needs_input`, `failed` or `in_progress`. |
| `structured_output` | Optional JSON, checked against the scenario's schema. |
| `usage` | Optional tokens and cost. AgentArena cannot observe these on its own, so they only appear in reports if you send them. |

## Calling tools

Tools are simulated by the scenario, which keeps every run repeatable. To call one, return it in `tool_calls`:

```json
{
  "message": { "content": "" },
  "tool_calls": [{ "id": "call_1", "name": "lookup_order", "arguments": { "order_id": "A1001" } }],
  "task_status": "in_progress"
}
```

AgentArena runs the simulation and calls your endpoint again with the conversation extended:

```json
{ "role": "assistant", "content": "", "tool_calls": [{ "id": "call_1", "name": "lookup_order", "arguments": { "order_id": "A1001" } }] },
{ "role": "tool", "name": "lookup_order", "tool_call_id": "call_1", "content": "{\"status\": \"shipped\"}" }
```

A failed tool returns its error in the same way, as `{"error": "..."}`. Handling that well is part of what the benchmark measures.

## Rules

- Answer within the agent version's request timeout, and within the run's overall timeout.
- Responses over 1 MB are rejected, and redirects are not followed.
- HTTP 5xx, connection errors and timeouts are retried up to the run's retry limit, so endpoints should tolerate repeated calls for the same `session_id`.
- Other non-200 responses and invalid JSON fail the execution without a retry.
- A scenario limits how many calls an agent can make. Exceeding it fails the execution.

## Authentication

Store a bearer token or API key on the agent with `PUT /v1/agents/{id}/credential`. It is encrypted at rest, never returned by the API, and sent only to your endpoint.

## Versioning

Compatible additions keep the major version, and unknown fields are ignored in both directions. A breaking change becomes version 2.0, and `POST /v1/agents/{id}/validate` reports the version your agent speaks.

## Try it

[`examples/sample-agent`](../../examples/sample-agent) implements this protocol in about 300 lines, and shares no code with AgentArena.
