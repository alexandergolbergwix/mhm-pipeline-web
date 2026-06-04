from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import require_admin
from app.auth.session import AuthContext
from app.crypto import pii
from app.db import get_session
from app.models.access_request import AccessRequest, STATUS_PENDING_ADMIN
from app.models.invitation import Invitation
from app.models.project import Membership, Project, PROJECT_ROLE_EDITOR, PROJECT_ROLE_OWNER
from app.models.session import Session
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, User
from app.schemas.admin import (
    AdminStats,
    ProjectListItem,
    ProjectTransferRequest,
    UserDetail,
    UserListItem,
    UserMembership,
    UserPatch,
)

router = APIRouter(tags=["admin"])


@router.get("/admin/stats", response_model=AdminStats)
async def get_stats(
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> AdminStats:
    pending_q = await db.execute(
        select(func.count()).select_from(AccessRequest).where(
            AccessRequest.status == STATUS_PENDING_ADMIN,
        ),
    )
    users_q = await db.execute(select(func.count()).select_from(User))
    projects_q = await db.execute(select(func.count()).select_from(Project))
    now = datetime.now(timezone.utc)
    invitations_q = await db.execute(
        select(func.count()).select_from(Invitation).where(
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > now,
        ),
    )
    return AdminStats(
        pending_access_requests=pending_q.scalar_one(),
        total_users=users_q.scalar_one(),
        total_projects=projects_q.scalar_one(),
        active_invitations=invitations_q.scalar_one(),
    )


@router.get("/admin/users", response_model=list[UserListItem])
async def list_users(
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> list[UserListItem]:
    result = await db.execute(select(User).order_by(User.created_at))
    users = result.scalars().all()
    items: list[UserListItem] = []
    for user in users:
        count_q = await db.execute(
            select(func.count()).select_from(Membership).where(
                Membership.user_id == user.id,
            ),
        )
        items.append(UserListItem(
            id=user.id,
            email=pii.decrypt_pii(user.email_encrypted),
            name=pii.decrypt_pii(user.name_encrypted),
            role=user.role,
            created_at=user.created_at,
            project_count=count_q.scalar_one(),
        ))
    return items


@router.get("/admin/users/{user_id}", response_model=UserDetail)
async def get_user(
    user_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> UserDetail:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    memberships_q = await db.execute(
        select(Membership, Project)
        .join(Project, Project.id == Membership.project_id)
        .where(Membership.user_id == user_id),
    )
    membership_rows = memberships_q.all()

    sessions_q = await db.execute(
        select(func.count()).select_from(Session).where(Session.user_id == user_id),
    )

    project_count_q = await db.execute(
        select(func.count()).select_from(Membership).where(Membership.user_id == user_id),
    )

    return UserDetail(
        id=user.id,
        email=pii.decrypt_pii(user.email_encrypted),
        name=pii.decrypt_pii(user.name_encrypted),
        role=user.role,
        created_at=user.created_at,
        project_count=project_count_q.scalar_one(),
        memberships=[
            UserMembership(
                project_id=m.project_id,
                project_name=p.name,
                role=m.role,
                joined_at=m.created_at,
            )
            for m, p in membership_rows
        ],
        active_session_count=sessions_q.scalar_one(),
    )


@router.patch("/admin/users/{user_id}", response_model=UserListItem)
async def patch_user(
    user_id: uuid.UUID,
    body: UserPatch,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> UserListItem:
    if body.role not in (ROLE_ADMIN, ROLE_EDITOR):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be 'admin' or 'editor'",
        )
    if auth.user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.role == ROLE_ADMIN and body.role != ROLE_ADMIN:
        admin_count_q = await db.execute(
            select(func.count()).select_from(User).where(User.role == ROLE_ADMIN),
        )
        if admin_count_q.scalar_one() <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last admin",
            )

    user.role = body.role
    await db.commit()
    await db.refresh(user)

    count_q = await db.execute(
        select(func.count()).select_from(Membership).where(Membership.user_id == user.id),
    )
    return UserListItem(
        id=user.id,
        email=pii.decrypt_pii(user.email_encrypted),
        name=pii.decrypt_pii(user.name_encrypted),
        role=user.role,
        created_at=user.created_at,
        project_count=count_q.scalar_one(),
    )


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> None:
    if auth.user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.role == ROLE_ADMIN:
        admin_count_q = await db.execute(
            select(func.count()).select_from(User).where(User.role == ROLE_ADMIN),
        )
        if admin_count_q.scalar_one() <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the last admin",
            )

    await db.delete(user)
    await db.commit()


@router.post("/admin/users/{user_id}/invalidate-sessions")
async def invalidate_sessions(
    user_id: uuid.UUID,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    sessions_q = await db.execute(select(Session).where(Session.user_id == user_id))
    sessions = sessions_q.scalars().all()
    for s in sessions:
        await db.delete(s)
    await db.commit()
    return {"ok": True}


@router.get("/admin/projects", response_model=list[ProjectListItem])
async def list_projects(
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> list[ProjectListItem]:
    result = await db.execute(select(Project).order_by(Project.created_at))
    projects = result.scalars().all()
    items: list[ProjectListItem] = []
    for project in projects:
        owner = await db.get(User, project.owner_id)
        count_q = await db.execute(
            select(func.count()).select_from(Membership).where(
                Membership.project_id == project.id,
            ),
        )
        items.append(ProjectListItem(
            id=project.id,
            name=project.name,
            description=project.description,
            owner_id=project.owner_id,
            owner_email=pii.decrypt_pii(owner.email_encrypted) if owner else "",
            owner_name=pii.decrypt_pii(owner.name_encrypted) if owner else "",
            member_count=count_q.scalar_one(),
            created_at=project.created_at,
        ))
    return items


@router.patch("/admin/projects/{project_id}/transfer", response_model=ProjectListItem)
async def transfer_project(
    project_id: uuid.UUID,
    body: ProjectTransferRequest,
    auth: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
) -> ProjectListItem:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    new_owner = await db.get(User, body.new_owner_id)
    if new_owner is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_owner_id does not exist",
        )

    old_owner_id = project.owner_id
    project.owner_id = body.new_owner_id

    existing_new_owner_q = await db.execute(
        select(Membership).where(
            Membership.project_id == project_id,
            Membership.user_id == body.new_owner_id,
        ),
    )
    existing_new_owner_m = existing_new_owner_q.scalar_one_or_none()
    if existing_new_owner_m is not None:
        existing_new_owner_m.role = PROJECT_ROLE_OWNER
    else:
        db.add(Membership(
            project_id=project_id,
            user_id=body.new_owner_id,
            role=PROJECT_ROLE_OWNER,
        ))

    existing_old_owner_q = await db.execute(
        select(Membership).where(
            Membership.project_id == project_id,
            Membership.user_id == old_owner_id,
        ),
    )
    existing_old_owner_m = existing_old_owner_q.scalar_one_or_none()
    if existing_old_owner_m is not None:
        existing_old_owner_m.role = PROJECT_ROLE_EDITOR
    else:
        db.add(Membership(
            project_id=project_id,
            user_id=old_owner_id,
            role=PROJECT_ROLE_EDITOR,
        ))

    await db.commit()
    await db.refresh(project)

    count_q = await db.execute(
        select(func.count()).select_from(Membership).where(
            Membership.project_id == project_id,
        ),
    )
    return ProjectListItem(
        id=project.id,
        name=project.name,
        description=project.description,
        owner_id=project.owner_id,
        owner_email=pii.decrypt_pii(new_owner.email_encrypted),
        owner_name=pii.decrypt_pii(new_owner.name_encrypted),
        member_count=count_q.scalar_one(),
        created_at=project.created_at,
    )
