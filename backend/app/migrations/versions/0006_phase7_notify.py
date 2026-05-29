"""phase7: NOTIFY trigger on project_events INSERT

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION notify_project_event() RETURNS trigger AS $$
        DECLARE
          payload text;
        BEGIN
          payload := json_build_object(
              'project_id', NEW.project_id,
              'event_id',   NEW.id,
              'type',       NEW.type,
              'actor_id',   NEW.actor_id,
              'created_at', NEW.created_at,
              'payload',    NEW.payload
          )::text;
          PERFORM pg_notify('project_events', payload);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_notify_project_event ON project_events;")
    op.execute(
        """
        CREATE TRIGGER trg_notify_project_event
        AFTER INSERT ON project_events
        FOR EACH ROW EXECUTE FUNCTION notify_project_event();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_notify_project_event ON project_events;")
    op.execute("DROP FUNCTION IF EXISTS notify_project_event();")
