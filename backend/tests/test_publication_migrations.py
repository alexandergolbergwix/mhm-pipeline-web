"""The deployed migration schema supports the publication repository queries."""
from importlib import import_module
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select

from app.models.publication import PublicationExecutionAction


def test_migrated_database_can_read_execution_actions():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            import_module("app.migrations.versions.0040_publication_core").upgrade()
            scripts = ScriptDirectory(str(Path(__file__).parents[1] / "app/migrations"))
            revisions = list(scripts.iterate_revisions("head", "0040_publication_core"))
            for revision in reversed(revisions):
                revision.module.upgrade()
        connection.execute(select(PublicationExecutionAction).limit(1)).all()
