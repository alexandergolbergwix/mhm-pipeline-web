"""Pydantic request / response shapes for the auth router."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class MeResponse(BaseModel):
    """The shape the frontend's auth store consumes after login / on /me."""

    id: uuid.UUID
    email: EmailStr
    name: str


class LoginResponse(MeResponse):
    """200 OK on successful login. The session cookie is set in the response
    headers; this body just confirms the identity to the client."""
