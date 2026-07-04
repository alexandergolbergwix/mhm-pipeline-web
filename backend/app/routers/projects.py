"""Projects + memberships router."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_perms import (
    ProjectContext,
    require_editor,
    require_owner,
    require_viewer,
)
from app.auth.session import AuthContext, current_auth
from app.crypto import index as idx
from app.crypto import pii
from app.db import get_session
from app.events import append_event
from app.models.project import (
    ALL_PROJECT_ROLES,
    PROJECT_ROLE_OWNER,
    Membership,
    Project,
)
from app.models.user import User
from app.schemas.projects import (
    MemberAddRequest,
    MemberItem,
    MemberRoleUpdate,
    ProjectCreateRequest,
    ProjectDetail,
    ProjectListItem,
    ProjectUpdateRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])


# ── List + create ─────────────────────────────────────────────────────


@router.get("", response_model=list[ProjectListItem])
async def list_my_projects(
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> list[ProjectListItem]:
    """Every project where the user is owner or a member."""
    role_col = case(
        (Project.owner_id == auth.user.id, "owner"),
        else_=Membership.role,
    ).label("role")
    stmt = (
        select(Project, role_col)
        .outerjoin(
            Membership,
            (Membership.project_id == Project.id) & (Membership.user_id == auth.user.id),
        )
        .where(
            or_(Project.owner_id == auth.user.id, Membership.user_id == auth.user.id),
        )
        .order_by(Project.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    project_ids = [p.id for p, _ in rows]

    counts: dict = {}
    if project_ids:
        rows_c = (
            await db.execute(
                select(Membership.project_id, func.count().label("c"))
                .where(Membership.project_id.in_(project_ids))
                .group_by(Membership.project_id)
            )
        ).all()
        counts = {pid: int(c) for pid, c in rows_c}

    return [
        ProjectListItem(
            id=p.id, name=p.name, description=p.description,
            role=role, member_count=counts.get(p.id, 0), created_at=p.created_at,
        )
        for p, role in rows
    ]


@router.post("", response_model=ProjectListItem, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_session),
) -> ProjectListItem:
    proj = Project(
        owner_id=auth.user.id, name=payload.name, description=payload.description,
    )
    db.add(proj)
    await db.flush()
    db.add(
        Membership(project_id=proj.id, user_id=auth.user.id, role=PROJECT_ROLE_OWNER),
    )
    await append_event(
        db, project_id=proj.id, actor_id=auth.user.id, type="project.created",
        payload={"name": proj.name},
    )
    await db.commit()
    return ProjectListItem(
        id=proj.id, name=proj.name, description=proj.description,
        role="owner", member_count=1, created_at=proj.created_at,
    )


# ── Detail / update / delete ──────────────────────────────────────────


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> ProjectDetail:
    members = await _list_members(db, ctx.project.id)
    return ProjectDetail(
        id=ctx.project.id, name=ctx.project.name, description=ctx.project.description,
        owner_id=ctx.project.owner_id, created_at=ctx.project.created_at,
        role=ctx.role, members=members,
    )


@router.patch("/{project_id}", response_model=ProjectDetail)
async def update_project(
    payload: ProjectUpdateRequest,
    ctx: ProjectContext = Depends(require_editor),
    db: AsyncSession = Depends(get_session),
) -> ProjectDetail:
    changes: dict[str, object] = {}
    if payload.name is not None and payload.name != ctx.project.name:
        changes["name"] = {"from": ctx.project.name, "to": payload.name}
        ctx.project.name = payload.name
    if payload.description is not None and payload.description != ctx.project.description:
        changes["description"] = {"changed": True}
        ctx.project.description = payload.description
    if changes:
        await append_event(
            db, project_id=ctx.project.id, actor_id=ctx.user_id, type="project.updated",
            payload=changes,
        )
    await db.commit()
    members = await _list_members(db, ctx.project.id)
    return ProjectDetail(
        id=ctx.project.id, name=ctx.project.name, description=ctx.project.description,
        owner_id=ctx.project.owner_id, created_at=ctx.project.created_at,
        role=ctx.role, members=members,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    ctx: ProjectContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
) -> None:
    await db.delete(ctx.project)
    await db.commit()


# ── Members ───────────────────────────────────────────────────────────


@router.get("/{project_id}/members", response_model=list[MemberItem])
async def list_members(
    ctx: ProjectContext = Depends(require_viewer),
    db: AsyncSession = Depends(get_session),
) -> list[MemberItem]:
    return await _list_members(db, ctx.project.id)


@router.post("/{project_id}/members", response_model=MemberItem, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberAddRequest,
    ctx: ProjectContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
) -> MemberItem:
    if payload.role not in ALL_PROJECT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role",
        )
    user = (
        await db.execute(
            select(User).where(User.email_index == idx.blind_index(payload.email))
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No user with that email — invite them to the org first",
        )
    if user.id == ctx.project.owner_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Owner is already a member",
        )
    existing = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == ctx.project.id,
                Membership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already a member",
        )
    db.add(Membership(project_id=ctx.project.id, user_id=user.id, role=payload.role))
    await append_event(
        db, project_id=ctx.project.id, actor_id=ctx.user_id, type="member.added",
        payload={"user_id": str(user.id), "role": payload.role},
    )
    await db.commit()
    return MemberItem(
        user_id=user.id,
        email=pii.decrypt_pii(user.email_encrypted),
        name=pii.decrypt_pii(user.name_encrypted),
        role=payload.role, is_owner=False,
    )


@router.patch(
    "/{project_id}/members/{user_id}", response_model=MemberItem,
)
async def update_member_role(
    user_id: uuid.UUID,
    payload: MemberRoleUpdate,
    ctx: ProjectContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
) -> MemberItem:
    if user_id == ctx.project.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner's role cannot be changed",
        )
    m = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == ctx.project.id, Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a member")
    m.role = payload.role
    await db.commit()
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return MemberItem(
        user_id=u.id,
        email=pii.decrypt_pii(u.email_encrypted),
        name=pii.decrypt_pii(u.name_encrypted),
        role=m.role, is_owner=False,
    )


@router.delete(
    "/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    user_id: uuid.UUID,
    ctx: ProjectContext = Depends(require_owner),
    db: AsyncSession = Depends(get_session),
) -> None:
    if user_id == ctx.project.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner cannot be removed (delete the project instead)",
        )
    m = (
        await db.execute(
            select(Membership).where(
                Membership.project_id == ctx.project.id, Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a member")
    await db.delete(m)
    await db.commit()


# ── helpers ───────────────────────────────────────────────────────────


async def _list_members(db: AsyncSession, project_id: uuid.UUID) -> list[MemberItem]:
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, Membership.user_id == User.id)
            .where(Membership.project_id == project_id)
            .order_by(Membership.created_at.asc())
        )
    ).all()
    owner_id = (
        await db.execute(select(Project.owner_id).where(Project.id == project_id))
    ).scalar_one()
    return [
        MemberItem(
            user_id=u.id,
            email=pii.decrypt_pii(u.email_encrypted),
            name=pii.decrypt_pii(u.name_encrypted),
            role=m.role, is_owner=(u.id == owner_id),
        )
        for m, u in rows
    ]
