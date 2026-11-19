"""AgentArena Agent Protocol v1: the HTTP contract an external agent implements.

AgentArena sends one ``AgentRequest`` per step and expects one ``AgentResponse``.
Agents never run inside AgentArena. Tools are simulated by the platform: an
agent asks for a tool through ``tool_calls`` and receives the result in the
next request, which keeps every run reproducible.

The JSON Schema in contracts/agent-protocol/ is generated from these models.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1.0"

TaskStatus = Literal["in_progress", "completed", "failed", "needs_input"]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    """Reported by the agent. AgentArena cannot observe tokens on its own."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    model: str | None = None
    cost_usd: float | None = Field(default=None, ge=0)


class AgentRequest(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    session_id: str
    turn: int = Field(ge=1)
    messages: list[ChatMessage]
    tools: list[ToolSpec] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["assistant"] = "assistant"
    content: str = ""


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protocol_version: str = PROTOCOL_VERSION
    message: AgentMessage = Field(default_factory=AgentMessage)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    task_status: TaskStatus = "completed"
    structured_output: Any = None
    usage: Usage | None = None
