"""Project-scoped RBAC helpers used by every project router."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import AuthContext, current_auth
from app.db import get_session
from app.models.project import (
    PROJECT_ROLE_EDITOR,
    PROJECT_ROLE_OWNER,
    Membership,
    Project,
)


@dataclass
class ProjectContext:
    """Resolved (project, requester's role) pair handed to handlers."""

    project: Project
    role: str
    auth: AuthContext

    @property
    def user_id(self) -> uuid.UUID:
        return self.auth.user.id


async def _resolve(
    db: AsyncSession, project_id: uuid.UUID, auth: AuthContext, *,
    required_roles: Iterable[str] | None,
) -> ProjectContext:
    proj = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Owner short-circuit — owners always have full access regardless of
    # what's in the memberships table (and the project creator gets an
    # owner Membership row anyway).
    if proj.owner_id == auth.user.id:
        return ProjectContext(project=proj, role=PROJECT_ROLE_OWNER, auth=auth)

    membership = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == proj.id, Membership.user_id == auth.user.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project",
        )

    if required_roles and membership.role not in required_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Required role(s): {','.join(required_roles)}",
        )

    return ProjectContext(project=proj, role=membership.role, auth=auth)


def require_project_role(*roles: str):
    """Dependency factory — requires the caller to hold one of *roles* on
    the URL's project (or to be the owner). Use as::

        @router.get("/projects/{project_id}")
        async def ...(ctx: ProjectContext = Depends(require_project_role("viewer", "editor", "owner"))):
    """

    async def _dep(
        project_id: uuid.UUID = Path(...),
        db: AsyncSession = Depends(get_session),
        auth: AuthContext = Depends(current_auth),
    ) -> ProjectContext:
        return await _resolve(db, project_id, auth, required_roles=set(roles))

    return _dep


# Convenience shorthands.
require_viewer = require_project_role(PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR, "viewer")
require_editor = require_project_role(PROJECT_ROLE_OWNER, PROJECT_ROLE_EDITOR)
require_owner = require_project_role(PROJECT_ROLE_OWNER)
