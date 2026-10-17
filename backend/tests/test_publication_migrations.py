"""The deployed migration schema supports the publication repository queries."""
from importlib import import_module
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text

from app.models.publication import PublicationExecutionAction, PublicationPlanAction


def test_migrated_database_can_read_execution_actions():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            import_module("app.migrations.versions.0040_publication_core").upgrade()
            # SQLite cannot build the pre-0040 schema (0004 uses JSONB), so the
            # legacy tables that revisions 0044+ ALTER exist here as empty
            # shells. Only table existence matters to op.add_column.
            for legacy_table in (
                "authority_matches",
                "hmo_studio_item_overrides",
                "wikidata_item_overrides",
                "run_jobs",
            ):
                connection.execute(
                    text(f"CREATE TABLE IF NOT EXISTS {legacy_table} (id INTEGER PRIMARY KEY)")
                )
            scripts = ScriptDirectory(str(Path(__file__).parents[1] / "app/migrations"))
            revisions = list(scripts.iterate_revisions("head", "0040_publication_core"))
            for revision in reversed(revisions):
                revision.module.upgrade()
        connection.execute(select(PublicationExecutionAction).limit(1)).all()
        connection.execute(
            select(PublicationPlanAction.reconcile_method).limit(1)
        ).all()
