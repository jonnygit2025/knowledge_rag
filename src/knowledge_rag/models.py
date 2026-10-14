"""Portable types for unit tests; production and integration tests use PostgreSQL."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

J = JSON().with_variant(JSONB(), "postgresql")


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Channel(Identity, Base):
    __tablename__ = "channels"
    youtube_channel_id: Mapped[str] = mapped_column(String(100), unique=True)
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Video(Identity, Base):
    __tablename__ = "videos"
    youtube_video_id: Mapped[str] = mapped_column(String(100), unique=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    acquisition_status: Mapped[str] = mapped_column(String(40), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)


class Document(Identity, Base):
    __tablename__ = "documents"
    source_key: Mapped[str] = mapped_column(Text, unique=True)
    source_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    video_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("videos.id"), unique=True)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_active_version")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Version(Identity, Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "content_hash"),)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[dict] = mapped_column(J, default=dict)
    normalized_text: Mapped[str] = mapped_column(Text)
    corrected_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(J, default=dict)
    pipeline_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Segment(Identity, Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (UniqueConstraint("version_id", "position"),)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    start_seconds: Mapped[float | None] = mapped_column(Float)
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class Correction(Identity, Base):
    __tablename__ = "corrections"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcript_segments.id"))
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="applied")
    model: Mapped[str] = mapped_column(String(100), default="glossary")
    prompt_version: Mapped[str] = mapped_column(String(100), default="glossary-v1")


class Chunk(Identity, Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("version_id", "position"),)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    chunker_version: Mapped[str] = mapped_column(String(100))


class ChunkSegment(Base):
    __tablename__ = "chunk_segments"
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"), primary_key=True)
    segment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcript_segments.id"), primary_key=True
    )


class DocumentLink(Identity, Base):
    __tablename__ = "document_links"
    __table_args__ = (UniqueConstraint("source_document_id", "encountered_url"),)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    encountered_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)


class Graph(Identity, Base):
    __tablename__ = "document_graphs"
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), unique=True)
    extractor_version: Mapped[str] = mapped_column(String(100), default="explicit-v1")
    status: Mapped[str] = mapped_column(String(40), default="ready")


class Entity(Identity, Base):
    __tablename__ = "entities"
    identity_key: Mapped[str] = mapped_column(Text, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(String(50))
    aliases: Mapped[list] = mapped_column(J, default=list)


class Mention(Identity, Base):
    __tablename__ = "entity_mentions"
    graph_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_graphs.id"), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), index=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"))
    quote: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="source_assertion")


class Relation(Identity, Base):
    __tablename__ = "relations"
    __table_args__ = (UniqueConstraint("subject_entity_id", "predicate", "object_entity_id"),)
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    predicate: Mapped[str] = mapped_column(String(100))
    object_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))


class Evidence(Identity, Base):
    __tablename__ = "relation_evidence"
    relation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relations.id"), index=True)
    graph_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_graphs.id"), index=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"))
    quote: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="source_assertion")
    confidence: Mapped[float | None] = mapped_column(Float)


class Job(Identity, Base):
    __tablename__ = "jobs"
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(J, default=dict)
    state: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class JobStep(Identity, Base):
    __tablename__ = "job_steps"
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Identity, Base):
    __tablename__ = "index_outbox"
    __table_args__ = (UniqueConstraint("version_id", "namespace", "action", "generation"),)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20), default="upsert")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_error: Mapped[str | None] = mapped_column(Text)


class Manifest(Base):
    __tablename__ = "index_manifest"
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(100), primary_key=True)
    state: Mapped[str] = mapped_column(String(40), default="pending")
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RagMap(Base):
    __tablename__ = "rag_id_map"
    backend: Mapped[str] = mapped_column(String(30), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(100), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_versions.id"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id"))


class ProviderCall(Identity, Base):
    __tablename__ = "provider_calls"
    cache_key: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(30), default="reserved")
    result: Mapped[dict | None] = mapped_column(J)
    usage: Mapped[dict | None] = mapped_column(J)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


Index("ix_job_due", Job.state, Job.next_attempt_at)
Index("ix_outbox_due", Outbox.state, Outbox.next_attempt_at)
