from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

RoleName = Literal["owner", "admin", "member", "viewer"]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)
    organization_name: str = Field(min_length=2, max_length=100)

    @field_validator("display_name", "organization_name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    organization_id: UUID | None = None


class SwitchOrganizationRequest(BaseModel):
    organization_id: UUID


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    created_at: datetime


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    created_at: datetime


class OrganizationMembershipOut(BaseModel):
    organization: OrganizationOut
    role: RoleName


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - a scheme name, not a secret
    expires_in: int
    user: UserOut
    organization: OrganizationOut
    role: RoleName


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: RoleName
    organizations: list[OrganizationMembershipOut]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class OrganizationUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: RoleName
    joined_at: datetime


class MemberAdd(BaseModel):
    email: EmailStr
    role: RoleName = "member"


class MemberUpdate(BaseModel):
    role: RoleName
