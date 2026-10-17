"""Approve a sealed Publication release and run its Dry-run (no live writes)."""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path


def _ensure_app_on_syspath() -> None:
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


_ensure_app_on_syspath()

from sqlalchemy import text  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.publication.credentials import (  # noqa: E402
    configured_publication_gateway_factory,
)
from app.publication.runtime import PublicationRuntime  # noqa: E402
from app.schemas.publication import (  # noqa: E402
    AdvancePublicationRequest,
)


async def main() -> None:
    run_id = uuid.UUID(sys.argv[1])
    publication_id = sys.argv[2]
    actor_id = sys.argv[3]
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "SELECT id, release_digest FROM publication_releases "
                    "WHERE publication_id=:p ORDER BY created_at DESC LIMIT 1"
                ),
                {"p": publication_id},
            )
        ).first()
        if row is None:
            raise SystemExit("no release")
        release_id, release_digest = str(row[0]), str(row[1])
        runtime = PublicationRuntime(
            session=db,
            gateway_factory=configured_publication_gateway_factory,
        )
        reviewed = await runtime.advance(
            run_id=run_id,
            publication_id=publication_id,
            actor_id=actor_id,
            request=AdvancePublicationRequest.model_validate(
                {
                    "command": {
                        "type": "review",
                        "release_id": release_id,
                        "expected_release_digest": release_digest,
                        "selection": {"mode": "eligible_release"},
                        "decision": "approve",
                        "reason": (
                            "Curator-approved: AI-verified Studio items, "
                            "clean 1-nodes release, two-phase local refs."
                        ),
                    }
                }
            ),
        )
        publication = reviewed.publication
        approval = publication.approval_set if publication else None
        if approval is None:
            raise RuntimeError("The review returned no approval set")
        print(
            f"approved: approval_set={approval.approval_set_id} "
            f"count={approval.approved_count}",
            flush=True,
        )
        checked = await runtime.advance(
            run_id=run_id,
            publication_id=publication_id,
            actor_id=actor_id,
            request=AdvancePublicationRequest.model_validate(
                {
                    "command": {
                        "type": "dry_run",
                        "force_refresh": True,
                        "approval_set_id": approval.approval_set_id,
                        "expected_approval_digest": approval.approval_digest,
                    }
                }
            ),
        )
        plan = (
            checked.publication.plan if checked.publication else None
        )
        if plan is None:
            raise RuntimeError("The dry-run returned no plan")
        print(
            f"dry-run: plan={plan.plan_id} status={plan.status} "
            f"actions={plan.action_counts}",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
