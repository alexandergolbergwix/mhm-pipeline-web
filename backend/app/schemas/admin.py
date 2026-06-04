from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AdminStats(BaseModel):
    pending_access_requests: int
    total_users: int
    total_projects: int
    active_invitations: int


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str
    created_at: datetime
    project_count: int


class UserMembership(BaseModel):
    project_id: uuid.UUID
    project_name: str
    role: str
    joined_at: datetime


class UserDetail(UserListItem):
    memberships: list[UserMembership]
    active_session_count: int


class UserPatch(BaseModel):
    role: str


class ProjectListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    owner_id: uuid.UUID
    owner_email: str
    owner_name: str
    member_count: int
    created_at: datetime


class ProjectTransferRequest(BaseModel):
    new_owner_id: uuid.UUID
