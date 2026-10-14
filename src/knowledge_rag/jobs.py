import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import and_, or_, select

from knowledge_rag.export import export_all
from knowledge_rag.ingestion.manual import read_manual
from knowledge_rag.ingestion.web import extract_urls, read_external
from knowledge_rag.ingestion.youtube import (
    TranscriptUnavailable,
    YouTubeMetadata,
    YouTubeTranscripts,
)
from knowledge_rag.models import (
    Channel,
    Document,
    DocumentLink,
    Job,
    JobStep,
    Manifest,
    Outbox,
    Version,
    Video,
    now,
)
from knowledge_rag.storage import add_link, queue_index, save_document, source_lock


def load_config(cfg):
    if not cfg.config_file.exists():
        return {"channels": [], "languages": ["it", "en"], "glossary": {}}
    value = yaml.safe_load(cfg.config_file.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("config.yaml must be a mapping")
    return value


def enqueue(session, kind, payload, key):
    source_lock(session, key)
    existing = session.scalar(select(Job).where(Job.dedupe_key == key))
    if existing:
        return existing
    job = Job(kind=kind, payload=payload, dedupe_key=key)
    session.add(job)
    session.flush()
    return job


def enqueue_sync(session, cfg):
    # One durable job per interval, shared by CLI, worker startup and UI.
    bucket = int(datetime.now(UTC).timestamp() // (cfg.sync_interval_minutes * 60))
    return enqueue(session, "sync", {}, f"sync:{bucket}")


def claim_job(factory, cfg):
    with factory.begin() as session:
        current = now()
        job = session.scalar(
            select(Job)
            .where(
                Job.attempts < cfg.max_job_attempts,
                or_(
                    and_(Job.state.in_(["pending", "retry"]), Job.next_attempt_at <= current),
                    and_(Job.state == "running", Job.lease_until < current),
                ),
            )
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.state, job.attempts = "running", job.attempts + 1
        job.lease_until = current + timedelta(seconds=cfg.job_lease_seconds)
        job.lease_token = str(uuid.uuid4())
        session.add(JobStep(job_id=job.id, attempt=job.attempts, state="started"))
        return {"id": job.id, "token": job.lease_token, "kind": job.kind, "payload": job.payload}


def safe_error(exc, cfg):
    message = f"{type(exc).__name__}: {str(exc)[:1500]}"
    for secret in (cfg.youtube_api_key, cfg.provider_api_key, cfg.qdrant_api_key):
        if secret.get_secret_value():
            message = message.replace(secret.get_secret_value(), "[redacted]")
    # Database/connection exceptions may include credentials in URL form.
    import re

    return re.sub(r"(\w+://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", message)


def finish_job(factory, cfg, claimed, error=None):
    with factory.begin() as session:
        job = session.get(Job, claimed["id"], with_for_update=True)
        if job.lease_token != claimed["token"]:
            return
        job.lease_until = None
        if error:
            job.last_error = safe_error(error, cfg)
            job.state = "failed" if job.attempts >= cfg.max_job_attempts else "retry"
            job.next_attempt_at = now() + timedelta(seconds=min(3600, 15 * 2**job.attempts))
        else:
            job.state, job.last_error = "done", None
        session.add(
            JobStep(job_id=job.id, attempt=job.attempts, state=job.state, detail=job.last_error)
        )


def sync_channels(factory, cfg, metadata=None):
    config = load_config(cfg)
    own = metadata is None
    metadata = metadata or YouTubeMetadata(cfg.youtube_api_key.get_secret_value())
    try:
        for item in config.get("channels", []):
            if not item.get("enabled", True):
                continue
            resolved = metadata.resolve_channel(item["reference"])
            with factory.begin() as session:
                source_lock(session, resolved["id"])
                channel = session.scalar(
                    select(Channel).where(Channel.youtube_channel_id == resolved["id"])
                )
                if channel is None:
                    channel = Channel(
                        youtube_channel_id=resolved["id"],
                        name=resolved["name"],
                        uploads_playlist_id=resolved["playlist"],
                    )
                    session.add(channel)
                    session.flush()
                channel_id, checked = channel.id, channel.last_checked_at
            # Cover a long offline interval as well as the configured lookback window.
            days = config.get("initial_days", 30)
            if checked:
                checked = checked.replace(tzinfo=UTC) if checked.tzinfo is None else checked
                days = max(days, (now() - checked).days + 2)
            for video_data in metadata.videos(
                resolved["playlist"],
                days=days,
                max_initial=config.get("initial_max_videos", 10),
                initial=checked is None,
            ):
                if video_data.pop("is_live", False) and not config.get("include_live", False):
                    continue
                video_data.pop(
                    "duration", None
                )  # Shorts are included; duration alone cannot identify them.
                with factory.begin() as session:
                    source_lock(session, video_data["youtube_video_id"])
                    video = session.scalar(
                        select(Video).where(
                            Video.youtube_video_id == video_data["youtube_video_id"]
                        )
                    )
                    if video is None:
                        video = Video(channel_id=channel_id, **video_data)
                        session.add(video)
                        session.flush()
                    if video.acquisition_status == "pending":
                        enqueue(
                            session,
                            "transcript",
                            {"video_id": str(video.id)},
                            f"transcript:{video.id}",
                        )
                    # Capture descriptions/references even if captions are unavailable.
                    document = session.scalar(
                        select(Document).where(
                            Document.source_key == f"youtube:{video.youtube_video_id}"
                        )
                    )
                    if document is None:
                        document = Document(
                            source_key=f"youtube:{video.youtube_video_id}",
                            source_type="youtube_transcript",
                            title=video.title,
                            url=video.url,
                            video_id=video.id,
                        )
                        session.add(document)
                        session.flush()
                    for encountered, canonical in extract_urls(video.description)[
                        : config.get("max_external_links", 5)
                    ]:
                        link = add_link(session, document, encountered, canonical)
                        if config.get("fetch_external", False) and link.status == "pending":
                            enqueue(
                                session,
                                "external",
                                {"link_id": str(link.id)},
                                f"external:{link.id}",
                            )
            with factory.begin() as session:
                session.get(Channel, channel_id).last_checked_at = now()
    finally:
        if own:
            metadata.close()


def acquire_transcript(factory, cfg, video_id, provider=None):
    with factory() as session:
        video = session.get(Video, uuid.UUID(video_id))
        channel = session.get(Channel, video.channel_id)
        data = {
            "source_key": f"youtube:{video.youtube_video_id}",
            "source_type": "youtube_transcript",
            "title": video.title,
            "url": video.url,
            "video_id": video.id,
            "metadata": {
                "channel_id": str(channel.id),
                "channel": channel.name,
                "published_at": video.published_at.isoformat(),
            },
        }
        external_id = video.youtube_video_id
    config = load_config(cfg)
    try:
        result = (provider or YouTubeTranscripts()).fetch(
            external_id, config.get("languages", ["it", "en"])
        )
    except TranscriptUnavailable as exc:
        with factory.begin() as session:
            video = session.get(Video, uuid.UUID(video_id))
            video.acquisition_status, video.last_error = exc.status, str(exc)
        return
    except Exception as exc:
        with factory.begin() as session:
            video = session.get(Video, uuid.UUID(video_id))
            video.acquisition_status, video.last_error = "temporary_error", safe_error(exc, cfg)
        raise
    data["segments"] = result["segments"]
    data["metadata"].update(result["metadata"])
    with factory.begin() as session:
        save_document(session, cfg, data, config.get("glossary", {}))
        video = session.get(Video, uuid.UUID(video_id))
        video.acquisition_status, video.last_error = "ready", None


def import_data(factory, cfg, data):
    with factory.begin() as session:
        # Manual subtitles can repair a known video's missing/blocked transcript.
        if data["source_key"].startswith("youtube:"):
            video = session.scalar(
                select(Video).where(Video.youtube_video_id == data["source_key"].split(":", 1)[1])
            )
            if video:
                data["video_id"], data["title"] = video.id, video.title
                channel = session.get(Channel, video.channel_id)
                data.setdefault("metadata", {}).update(
                    {
                        "channel_id": str(channel.id),
                        "channel": channel.name,
                        "published_at": video.published_at.isoformat(),
                    }
                )
                video.acquisition_status, video.last_error = "ready", None
        document, version = save_document(session, cfg, data, load_config(cfg).get("glossary", {}))
        return document.id, version.id


def load_demo(factory, cfg, fixture=Path("tests/fixtures/demo.json")):
    content = json.loads(Path(fixture).read_text(encoding="utf-8"))
    for data in content:
        import_data(factory, cfg, data)


def execute_job(factory, cfg, claimed):
    kind, payload = claimed["kind"], claimed["payload"]
    if kind == "sync":
        sync_channels(factory, cfg)
    elif kind == "transcript":
        acquire_transcript(factory, cfg, payload["video_id"])
    elif kind == "import":
        root = cfg.import_dir.resolve()
        path = (root / payload["filename"]).resolve()
        if path.parent != root:
            raise ValueError("Import path must be inside the configured import directory")
        data = read_manual(
            path,
            source_key=payload.get("source_key"),
            title=payload.get("title"),
            video_id=payload.get("youtube_video_id"),
        )
        import_data(factory, cfg, data)
    elif kind == "external":
        with factory() as session:
            link = session.get(DocumentLink, uuid.UUID(payload["link_id"]))
            url = link.canonical_url
        try:
            data = read_external(url)
            with factory.begin() as session:
                document, _ = save_document(session, cfg, data)
                link = session.get(DocumentLink, uuid.UUID(payload["link_id"]))
                link.target_document_id, link.status, link.last_error = document.id, "ready", None
        except Exception as exc:
            with factory.begin() as session:
                link = session.get(DocumentLink, uuid.UUID(payload["link_id"]))
                link.status, link.last_error = "error", safe_error(exc, cfg)
            raise
    else:
        raise ValueError(f"Unknown job kind: {kind}")


def pump_outbox(factory, cfg, index):
    with factory.begin() as session:
        event = session.scalar(
            select(Outbox)
            .where(
                Outbox.state.in_(["pending", "retry"]),
                Outbox.next_attempt_at <= now(),
                Outbox.attempts < cfg.max_job_attempts,
            )
            .order_by(Outbox.generation, Outbox.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is None:
            return False
        event.attempts += 1
        try:
            with session.begin_nested():
                index.apply(session, event)
            event.state, event.last_error = "done", None
        except Exception as exc:
            event.state = "failed" if event.attempts >= cfg.max_job_attempts else "retry"
            event.last_error = safe_error(exc, cfg)
            event.next_attempt_at = now() + timedelta(seconds=min(3600, 15 * 2**event.attempts))
        return True


def retry_job(session, job_id):
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise ValueError("Unknown job")
    if job.lease_until and job.lease_until.tzinfo is None:
        job.lease_until = job.lease_until.replace(tzinfo=UTC)
    if job.state == "running" and job.lease_until and job.lease_until > now():
        raise ValueError("Job is still running")
    job.state, job.attempts, job.next_attempt_at = "pending", 0, now()
    job.lease_token = None


def reconcile(factory, cfg, index):
    # Check every expected point, not merely collection counts.
    from knowledge_rag.models import Chunk
    from knowledge_rag.vectors import point_id

    with factory.begin() as session:
        source_lock(session, "reconcile")
        for document in session.scalars(
            select(Document).where(
                Document.enabled.is_(True), Document.active_version_id.is_not(None)
            )
        ):
            ids = list(
                session.scalars(
                    select(Chunk.id).where(Chunk.version_id == document.active_version_id)
                )
            )
            expected = [point_id(cid, cfg.namespace) for cid in ids]
            found = set()
            if index.client.collection_exists(cfg.namespace):
                for start in range(0, len(expected), 100):
                    found.update(
                        str(p.id)
                        for p in index.client.retrieve(cfg.namespace, expected[start : start + 100])
                    )
            if set(expected) != found:
                queue_index(session, document.active_version_id, cfg.namespace, force=True)
        for manifest in session.scalars(select(Manifest)):
            version = session.get(Version, manifest.version_id)
            doc = session.get(Document, version.document_id)
            if not doc.enabled or doc.active_version_id != version.id:
                queue_index(session, version.id, manifest.namespace, "delete", force=True)


def worker(factory, cfg, index, once=False):
    next_sync = 0
    while True:
        if cfg.startup_sync and time.monotonic() >= next_sync:
            with factory.begin() as session:
                enqueue_sync(session, cfg)
            next_sync = time.monotonic() + cfg.sync_interval_minutes * 60
        claimed = claim_job(factory, cfg)
        if claimed:
            stop_heartbeat = threading.Event()

            def heartbeat():
                while not stop_heartbeat.wait(cfg.job_lease_seconds / 3):
                    try:
                        with factory.begin() as session:
                            job = session.get(Job, claimed["id"], with_for_update=True)
                            if job.lease_token != claimed["token"] or job.state != "running":
                                return
                            job.lease_until = now() + timedelta(seconds=cfg.job_lease_seconds)
                    except Exception as exc:
                        print("Job heartbeat failed:", safe_error(exc, cfg), flush=True)

            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                execute_job(factory, cfg, claimed)
                finish_job(factory, cfg, claimed)
            except Exception as exc:
                finish_job(factory, cfg, claimed, exc)
            finally:
                stop_heartbeat.set()
                thread.join(timeout=2)
        indexed = pump_outbox(factory, cfg, index)
        if claimed or indexed:
            # Exports are derived and have their own retry command; never invalidate canonically saved data.
            try:
                with factory() as session:
                    export_all(session, cfg.export_dir)
            except OSError as exc:
                print("Export failed:", safe_error(exc, cfg), flush=True)
        if once:
            return
        if not claimed and not indexed:
            time.sleep(2)
