from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

AgentType = Literal["customer_support", "coding", "research", "sales", "operations", "generic"]
AgentStatus = Literal["draft", "active", "unreachable", "disabled"]
IntegrationType = Literal["http_v1"]
AuthType = Literal["bearer", "api_key"]

_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_FORBIDDEN_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key", "host", "content-length"}


class VersionConfig(BaseModel):
    endpoint_url: str = Field(min_length=8, max_length=2048)
    integration_type: IntegrationType = "http_v1"
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)
    default_headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    model_name: str | None = Field(default=None, max_length=120)
    input_cost_per_1k: Decimal | None = Field(default=None, ge=0)
    output_cost_per_1k: Decimal | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=2000)

    @field_validator("default_headers")
    @classmethod
    def no_secret_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        for name, value in headers.items():
            if not _HEADER_NAME.match(name) or len(value) > 512:
                raise ValueError(f"Invalid header: {name}")
            if name.lower() in _FORBIDDEN_HEADERS:
                raise ValueError(f"{name} must be set through the credential, not default_headers")
        return headers


class AgentCreate(VersionConfig):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=4000)
    agent_type: AgentType = "generic"


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=4000)
    agent_type: AgentType | None = None
    status: Literal["draft", "disabled"] | None = None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    version: int
    endpoint_url: str
    integration_type: str
    request_timeout_seconds: int
    default_headers: dict[str, str]
    model_name: str | None
    input_cost_per_1k: Decimal | None
    output_cost_per_1k: Decimal | None
    notes: str
    created_at: datetime


class CredentialIn(BaseModel):
    auth_type: AuthType
    secret: str = Field(min_length=1, max_length=4096)
    header_name: str = Field(default="Authorization", pattern=r"^[A-Za-z0-9-]{1,64}$")


class CredentialOut(BaseModel):
    """Describes a credential without ever revealing it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auth_type: AuthType
    header_name: str
    key_id: str
    updated_at: datetime


class AgentOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    description: str
    agent_type: str
    status: AgentStatus
    latest_version: int
    endpoint_url: str
    credential: CredentialOut | None
    last_validated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentList(BaseModel):
    items: list[AgentOut]
    total: int


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class ValidationResult(BaseModel):
    valid: bool
    status: AgentStatus
    version: int
    latency_ms: float | None = None
    checks: list[ValidationCheck]


class ResolvedAgent(BaseModel):
    """Internal only: the one response that contains a decrypted credential."""

    agent_id: UUID
    version: int
    endpoint_url: str
    integration_type: str
    request_timeout_seconds: int
    headers: dict[str, str]
    model_name: str | None
    input_cost_per_1k: float | None
    output_cost_per_1k: float | None
