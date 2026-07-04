"""notify_broadcast_trim: shrink pg_notify payload to avoid 8 KB limit

Revision ID: 0027_notify_broadcast_trim
Revises: 0026_hmo_studio_item_cache
Create Date: 2026-07-04
"""

from __future__ import annotations

from alembic import op

revision = "0027_notify_broadcast_trim"
down_revision = "0026_hmo_studio_item_cache"
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
              'created_at', NEW.created_at
          )::text;
          PERFORM pg_notify('project_events', payload);
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
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
