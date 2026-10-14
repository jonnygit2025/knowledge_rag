"""Canonical baseline, using an immutable schema snapshot."""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None


def snapshot():
    spec = importlib.util.spec_from_file_location(
        "schema_v1", Path(__file__).parents[1] / "schema_v1.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Base.metadata


def upgrade():
    snapshot().create_all(op.get_bind())
    op.execute("""
        CREATE FUNCTION kr_reject_revision_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Canonical revisions are immutable; create a new version'; END;
        $$
    """)
    for table in (
        "document_versions",
        "transcript_segments",
        "chunks",
        "chunk_segments",
        "corrections",
        "document_graphs",
        "entity_mentions",
        "relation_evidence",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION kr_reject_revision_change()"
        )
    op.execute("""
        CREATE FUNCTION kr_check_active_version() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.active_version_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM document_versions v WHERE v.id = NEW.active_version_id
            AND v.document_id = NEW.id
          ) THEN RAISE EXCEPTION 'Active version must belong to this document'; END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute(
        "CREATE CONSTRAINT TRIGGER active_version_owner AFTER INSERT OR UPDATE ON documents "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION kr_check_active_version()"
    )
    op.execute("""
        CREATE FUNCTION kr_check_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM document_graphs g JOIN chunks c ON c.version_id = g.version_id
            WHERE g.id = NEW.graph_id AND c.id = NEW.chunk_id
            AND length(NEW.quote) > 0 AND position(NEW.quote in c.text) > 0
          ) THEN RAISE EXCEPTION 'Evidence must quote a chunk from the same graph version'; END IF;
          RETURN NEW;
        END; $$
    """)
    for table in ("entity_mentions", "relation_evidence"):
        op.execute(
            f"CREATE TRIGGER evidence_{table} BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION kr_check_evidence()"
        )


def downgrade():
    op.execute("DROP FUNCTION kr_check_evidence() CASCADE")
    op.execute("DROP TRIGGER active_version_owner ON documents")
    op.execute("DROP FUNCTION kr_check_active_version()")
    op.execute("DROP FUNCTION kr_reject_revision_change() CASCADE")
    snapshot().drop_all(op.get_bind())
