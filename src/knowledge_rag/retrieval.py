import uuid
from datetime import UTC

from qdrant_client import models
from sqlalchemy import or_, select

from knowledge_rag.models import (
    Chunk,
    ChunkSegment,
    Document,
    Entity,
    Evidence,
    Graph,
    Mention,
    Relation,
    Segment,
    Version,
)


def retrieve(session, index, question, limit=5, channel_id=None, published_after=None, tag=None):
    conditions = []
    if channel_id:
        conditions.append(
            models.FieldCondition(key="channel_id", match=models.MatchValue(value=str(channel_id)))
        )
    if tag:
        conditions.append(models.FieldCondition(key="tags", match=models.MatchValue(value=tag)))
    filters = models.Filter(must=conditions) if conditions else None
    hits = index.search(question, limit=max(100, limit * 20), filters=filters)
    candidates = []
    for hit in hits:
        if hit.score > 0:
            try:
                candidates.append(uuid.UUID(hit.payload["chunk_id"]))
            except (ValueError, KeyError, TypeError):
                continue
    # Canonical graph expansion: mentions with explicit identities, active evidence only.
    for entity in session.scalars(select(Entity)):
        if entity.canonical_name.casefold() in question.casefold():
            candidates += list(
                session.scalars(
                    select(Mention.chunk_id)
                    .join(Graph)
                    .join(
                        Document,
                        Document.active_version_id == Graph.version_id,
                    )
                    .where(Mention.entity_id == entity.id, Document.enabled.is_(True))
                )
            )
            candidates += list(
                session.scalars(
                    select(Evidence.chunk_id)
                    .join(Graph)
                    .join(
                        Document,
                        Document.active_version_id == Graph.version_id,
                    )
                    .where(
                        Document.enabled.is_(True),
                        Evidence.relation_id.in_(
                            select(Relation.id).where(
                                or_(
                                    Relation.subject_entity_id == entity.id,
                                    Relation.object_entity_id == entity.id,
                                )
                            )
                        ),
                    )
                )
            )
    citations = []
    for chunk_id in dict.fromkeys(candidates):
        row = session.execute(
            select(Chunk, Version, Document)
            .select_from(Chunk)
            .join(Version, Version.id == Chunk.version_id)
            .join(
                Document,
                Document.id == Version.document_id,
            )
            .where(
                Chunk.id == chunk_id,
                Document.enabled.is_(True),
                Document.active_version_id == Chunk.version_id,
            )
        ).first()
        if not row:
            continue
        chunk, version, document = row
        metadata = version.metadata_json
        if channel_id and metadata.get("channel_id") != str(channel_id):
            continue
        if tag and tag not in metadata.get("tags", []):
            continue
        if published_after:
            from datetime import datetime

            date = metadata.get("published_at")
            if not date or datetime.fromisoformat(
                date.replace("Z", "+00:00")
            ) < published_after.astimezone(UTC):
                continue
        start = session.scalar(
            select(Segment.start_seconds)
            .join(ChunkSegment)
            .where(
                ChunkSegment.chunk_id == chunk.id,
                Segment.start_seconds.is_not(None),
            )
            .order_by(Segment.start_seconds)
        )
        url = document.url
        if start is not None and document.source_type == "youtube_transcript":
            url += ("&" if "?" in url else "?") + f"t={int(start)}s"
        citations.append(
            {
                "document_id": str(document.id),
                "version_id": str(version.id),
                "chunk_id": str(chunk.id),
                "title": document.title,
                "url": url,
                "text": chunk.text,
                "start_seconds": start,
            }
        )
        if len(citations) >= limit:
            break
    return citations
