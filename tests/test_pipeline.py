import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from knowledge_rag.export import export_all
from knowledge_rag.graph import graph_rows
from knowledge_rag.jobs import (
    claim_job,
    enqueue,
    finish_job,
    load_demo,
    pump_outbox,
    reconcile,
)
from knowledge_rag.models import (
    Chunk,
    ChunkSegment,
    Document,
    Evidence,
    Job,
    Manifest,
    Outbox,
    Relation,
    Segment,
    Version,
    now,
)
from knowledge_rag.retrieval import retrieve
from knowledge_rag.storage import queue_index, save_document, withdraw_document


def fixture_data():
    return json.loads(Path("tests/fixtures/demo.json").read_text(encoding="utf-8"))


def drain(factory, cfg, index):
    for _ in range(100):
        if not pump_outbox(factory, cfg, index):
            return
    raise AssertionError("Outbox did not drain")


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_demo_idempotent_and_cited(factory, cfg, index):
    load_demo(factory, cfg)
    load_demo(factory, cfg)
    with factory() as session:
        assert count(session, Document) == count(session, Version) == 3
        assert count(session, Outbox) == 3
    drain(factory, cfg, index)
    drain(factory, cfg, index)
    with factory() as session:
        assert index.client.count(cfg.namespace, exact=True).count == count(session, Chunk)
        citations = retrieve(session, index, "Come Qdrant indicizza i chunk?")
        assert citations
        for item in citations:
            import uuid

            assert session.get(
                Document, uuid.UUID(item["document_id"])
            ).active_version_id == uuid.UUID(item["version_id"])
        answer = index.provider.answer("Qdrant", citations)
        assert "[1]" in answer
        assert graph_rows(session)


def test_originals_corrections_and_temporal_mapping(factory, cfg):
    data = {
        "source_key": "test:correction",
        "source_type": "youtube_transcript",
        "title": "Test",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "segments": [{"text": "  git hub  conserva codice. ", "start": 12, "duration": 9}],
    }
    with factory.begin() as session:
        doc, version = save_document(session, cfg, data, {"git hub": "GitHub"})
        assert version.raw_text == data["segments"][0]["text"]
        assert version.corrected_text == "GitHub conserva codice."
        segment = session.scalar(select(Segment))
        mapping = session.scalar(select(ChunkSegment))
        assert mapping.segment_id == segment.id
        assert segment.start_seconds == 12
        first_version = version.id
    with factory.begin() as session:
        changed = copy.deepcopy(data)
        changed["segments"][0]["text"] += " Nuovo contenuto."
        doc, version = save_document(session, cfg, changed, {"git hub": "GitHub"})
        assert version.id != first_version
        assert session.get(Version, first_version).raw_text == data["segments"][0]["text"]


def test_inactive_points_never_cited_and_upserts_retry(factory, cfg, index):
    data = fixture_data()[0]
    with factory.begin() as session:
        doc, old = save_document(session, cfg, data)
        doc_id, old_id = doc.id, old.id
    drain(factory, cfg, index)
    with factory.begin() as session:
        changed = copy.deepcopy(data)
        changed["segments"][-1]["text"] += " Nuova versione attiva."
        doc, current = save_document(session, cfg, changed)
        current_id = current.id
    # Qdrant still has old version; canonical active-version validation rejects it.
    with factory() as session:
        assert retrieve(session, index, "Qdrant")
        assert all(item["version_id"] != str(old_id) for item in retrieve(session, index, "Qdrant"))
    drain(factory, cfg, index)
    with factory.begin() as session:
        queue_index(session, current_id, cfg.namespace, force=True)
    drain(factory, cfg, index)
    with factory() as session:
        assert session.get(Document, doc_id).active_version_id == current_id
        assert index.client.count(cfg.namespace, exact=True).count == session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.version_id == current_id)
        )


def test_graph_withdrawal_preserves_other_sources(factory, cfg, index):
    data = fixture_data()
    with factory.begin() as session:
        first, _ = save_document(session, cfg, data[0])
        second, _ = save_document(session, cfg, data[1])
        first_id, second_id = first.id, second.id
    drain(factory, cfg, index)
    with factory.begin() as session:
        assert len([r for r in graph_rows(session) if r[1] == "indexes"]) == 2
        withdraw_document(session, first_id, cfg)
    with factory() as session:
        rows = [r for r in graph_rows(session) if r[1] == "indexes"]
        assert len(rows) == 1 and rows[0][5] == second_id
        assert count(session, Evidence) > len(rows)  # History is kept, not physically deleted.
        assert count(session, Relation) > 0
        assert all(c["document_id"] != str(first_id) for c in retrieve(session, index, "Qdrant"))
    drain(factory, cfg, index)


def test_invalid_evidence_rolls_back_entire_document(factory, cfg):
    data = fixture_data()[0]
    data["relations"][0]["quote"] = "This quote was invented."
    with pytest.raises(ValueError), factory.begin() as session:
        save_document(session, cfg, data)
    with factory() as session:
        assert count(session, Document) == count(session, Outbox) == 0


def test_outbox_failure_preserves_original_then_retries(factory, cfg, index):
    load_demo(factory, cfg)

    class FailingIndex:
        def apply(self, session, event):
            raise ConnectionError("Qdrant is offline")

    pump_outbox(factory, cfg, FailingIndex())
    with factory.begin() as session:
        failed = session.scalar(select(Outbox).where(Outbox.state == "retry"))
        assert failed.last_error and count(session, Version) == 3
        failed.next_attempt_at = now() - timedelta(seconds=1)
    drain(factory, cfg, index)
    with factory() as session:
        assert all(m.state == "ready" for m in session.scalars(select(Manifest)))


def test_job_lease_recovery_and_fencing(factory, cfg):
    with factory.begin() as session:
        first = enqueue(session, "sync", {}, "sync:test")
        second = enqueue(session, "sync", {}, "sync:test")
        assert first.id == second.id
    old = claim_job(factory, cfg)
    assert old and claim_job(factory, cfg) is None
    with factory.begin() as session:
        session.get(Job, old["id"]).lease_until = now() - timedelta(seconds=1)
    new = claim_job(factory, cfg)
    assert old["token"] != new["token"]
    finish_job(factory, cfg, old)
    with factory() as session:
        assert session.get(Job, old["id"]).state == "running"
    finish_job(factory, cfg, new)
    with factory() as session:
        assert session.get(Job, old["id"]).state == "done"


def test_reconciliation_missing_point(factory, cfg, index):
    load_demo(factory, cfg)
    drain(factory, cfg, index)
    points, _ = index.client.scroll(cfg.namespace, limit=1)
    index.client.delete(cfg.namespace, [points[0].id], wait=True)
    reconcile(factory, cfg, index)
    drain(factory, cfg, index)
    with factory() as session:
        assert index.client.count(cfg.namespace, exact=True).count == count(session, Chunk)


def test_exports_regenerated_and_withdrawn_files_retired(factory, cfg):
    load_demo(factory, cfg)
    with factory() as session:
        export_all(session, cfg.export_dir)
        doc = session.scalar(select(Document))
        doc_id = doc.id
    path = cfg.export_dir / f"{doc_id}.md"
    assert path.exists() and "version_id:" in path.read_text()
    path.write_text("Edited manually", encoding="utf-8")
    with factory() as session:
        export_all(session, cfg.export_dir)
    assert "Edited manually" not in path.read_text()
    with factory.begin() as session:
        withdraw_document(session, doc_id, cfg)
    with factory() as session:
        export_all(session, cfg.export_dir)
    assert not path.exists()


def test_namespace_isolation(cfg):
    other = cfg.model_copy(update={"embedding_provider": "openai-compatible"})
    assert other.namespace != cfg.namespace
    assert other.model_copy(update={"embedding_dimensions": 1024}).namespace != other.namespace
