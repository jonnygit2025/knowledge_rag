"""One transaction owns originals, versions, graph evidence, and indexing events."""

import uuid

from sqlalchemy import select, text

from knowledge_rag.graph import build_graph
from knowledge_rag.models import (
    Chunk,
    ChunkSegment,
    Correction,
    Document,
    DocumentLink,
    Manifest,
    Outbox,
    Segment,
    Version,
    now,
)
from knowledge_rag.processing import chunk_segments, digest, fingerprint, normalize, process_segment


def source_lock(session, source_key):
    if session.bind.dialect.name == "postgresql":
        # Serializes concurrent imports of the same source and shared entity creation.
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 918264})


def queue_index(session, version_id, namespace, action="upsert", force=False):
    old = session.scalars(
        select(Outbox)
        .where(
            Outbox.version_id == version_id,
            Outbox.namespace == namespace,
            Outbox.action == action,
        )
        .order_by(Outbox.generation.desc())
    ).first()
    if old and not force:
        return old
    generation = old.generation + 1 if old else 0
    event = Outbox(version_id=version_id, namespace=namespace, action=action, generation=generation)
    session.add(event)
    manifest = session.get(Manifest, (version_id, namespace))
    if manifest is None:
        session.add(Manifest(version_id=version_id, namespace=namespace))
    elif action == "upsert":
        manifest.state = "pending"
    return event


def save_document(session, cfg, data, glossary=None):
    source_lock(session, data["source_key"])
    glossary = glossary or {}
    document = session.scalar(select(Document).where(Document.source_key == data["source_key"]))
    if document is None:
        document = Document(
            source_key=data["source_key"],
            source_type=data["source_type"],
            title=data["title"],
            url=data["url"],
            video_id=data.get("video_id"),
        )
        session.add(document)
        session.flush()
    document.last_checked_at = now()
    document.title = data["title"]
    document.url = data["url"]
    document.enabled = True
    raw_segments = data.get("segments") or [{"text": data["text"]}]
    processed = [process_segment(item["text"], glossary) for item in raw_segments]
    content = " ".join(item.text for item in processed if item.text)
    if not content:
        raise ValueError("Document has no usable text")
    pipeline = f"pipeline-v1-segments-v1-{cfg.chunk_chars}-glossary-{fingerprint(glossary)[:12]}"
    metadata = {
        **data.get("metadata", {}),
        "source_title": data["title"],
        "source_url": data["url"],
        "processing_config": {"glossary": glossary, "chunk_chars": cfg.chunk_chars},
    }
    checksum = fingerprint(
        {
            "segments": raw_segments,
            "pipeline": pipeline,
            "text": data.get("text"),
            "metadata": metadata,
            "assertions": data.get("relations", []),
        }
    )
    version = session.scalar(
        select(Version).where(
            Version.document_id == document.id,
            Version.content_hash == checksum,
        )
    )
    old_version_id = document.active_version_id
    if version is None:
        version = Version(
            document_id=document.id,
            raw_text=data.get("text", "\n".join(s["text"] for s in raw_segments)),
            raw_json={"segments": raw_segments, "source": data.get("raw", {})},
            normalized_text=" ".join(normalize(s["text"]) for s in raw_segments),
            corrected_text=content,
            content_hash=checksum,
            metadata_json=metadata,
            pipeline_version=pipeline,
        )
        session.add(version)
        session.flush()
        segments = []
        for position, (raw, result) in enumerate(zip(raw_segments, processed, strict=True)):
            segment = Segment(
                version_id=version.id,
                position=position,
                raw_text=raw["text"],
                start_seconds=raw.get("start"),
                duration_seconds=raw.get("duration"),
            )
            session.add(segment)
            session.flush()
            segments.append(segment)
            for patch in result.corrections:
                session.add(
                    Correction(
                        version_id=version.id,
                        segment_id=segment.id,
                        **patch,
                        prompt_version=pipeline,
                    )
                )
        chunks = []
        for position, (body, indices) in enumerate(
            chunk_segments([p.text for p in processed], cfg.chunk_chars)
        ):
            chunk = Chunk(
                version_id=version.id,
                position=position,
                text=body,
                text_hash=digest(body),
                chunker_version=f"segments-v1-{cfg.chunk_chars}",
            )
            session.add(chunk)
            session.flush()
            chunks.append(chunk)
            session.add_all(
                [ChunkSegment(chunk_id=chunk.id, segment_id=segments[i].id) for i in indices]
            )
        build_graph(session, document, version, chunks, data.get("relations", []))
    document.active_version_id = version.id
    queue_index(
        session,
        version.id,
        cfg.namespace,
        force=old_version_id is not None and old_version_id != version.id,
    )
    if old_version_id and old_version_id != version.id:
        for manifest in session.scalars(
            select(Manifest).where(Manifest.version_id == old_version_id)
        ):
            queue_index(session, old_version_id, manifest.namespace, "delete", force=True)
    return document, version


def withdraw_document(session, document_id, cfg):
    source_lock(session, str(document_id))
    document = session.get(Document, uuid.UUID(str(document_id)))
    if not document:
        raise ValueError("Unknown document")
    document.enabled = False
    for version in session.scalars(select(Version).where(Version.document_id == document.id)):
        for manifest in session.scalars(select(Manifest).where(Manifest.version_id == version.id)):
            queue_index(session, version.id, manifest.namespace, "delete", force=True)


def add_link(session, document, encountered, canonical):
    link = session.scalar(
        select(DocumentLink).where(
            DocumentLink.source_document_id == document.id,
            DocumentLink.encountered_url == encountered,
        )
    )
    if link is None:
        link = DocumentLink(
            source_document_id=document.id, encountered_url=encountered, canonical_url=canonical
        )
        session.add(link)
        session.flush()
    return link
