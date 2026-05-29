"""Pydantic shapes for projects + memberships."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

ProjectRole = Literal["owner", "editor", "viewer"]


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ProjectListItem(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    role: ProjectRole          # the *requester's* role on this project
    member_count: int
    created_at: datetime


class MemberItem(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    name: str
    role: ProjectRole
    is_owner: bool


class ProjectDetail(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    owner_id: uuid.UUID
    created_at: datetime
    role: ProjectRole          # the *requester's* role
    members: list[MemberItem]


class MemberAddRequest(BaseModel):
    email: EmailStr
    role: ProjectRole = "editor"


class MemberRoleUpdate(BaseModel):
    role: ProjectRole
