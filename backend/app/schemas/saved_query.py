"""Pydantic schemas for SavedQuery (Feature 2)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SavedQueryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    query: str = Field(..., min_length=1)
    params: dict[str, Any] = {}


class SavedQueryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    query: str | None = Field(None, min_length=1)
    params: dict[str, Any] | None = None


class SavedQueryOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    created_by: uuid.UUID | None
    name: str
    description: str
    query: str
    params: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
