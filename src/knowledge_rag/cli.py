import uuid
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from knowledge_rag.config import settings
from knowledge_rag.db import session_factory
from knowledge_rag.export import export_all
from knowledge_rag.ingestion.manual import read_manual
from knowledge_rag.jobs import (
    enqueue_sync,
    import_data,
    load_demo,
    reconcile,
    retry_job,
    worker,
)
from knowledge_rag.models import Job, Outbox, now
from knowledge_rag.providers import Providers
from knowledge_rag.retrieval import retrieve
from knowledge_rag.storage import withdraw_document
from knowledge_rag.vectors import VectorIndex

app = typer.Typer(no_args_is_help=True)


def services():
    cfg = settings()
    factory = session_factory()
    return cfg, factory, VectorIndex(cfg, Providers(cfg, factory))


@app.command()
def migrate():
    """Apply committed PostgreSQL schema migrations."""
    command.upgrade(Config("alembic.ini"), "head")


@app.command()
def demo():
    """Idempotently load three clearly synthetic documents; no external requests."""
    load_demo(session_factory(), settings())
    typer.echo("Demo caricata. Il worker aggiornerà Qdrant e gli export.")


@app.command("import")
def import_file(path: Path, source_key: str | None = None, youtube_video_id: str | None = None):
    """Import TXT/MD/SRT/VTT or a structured JSON document."""
    data = read_manual(path, source_key=source_key, video_id=youtube_video_id)
    doc_id, version_id = import_data(session_factory(), settings(), data)
    typer.echo(f"document_id={doc_id}\nversion_id={version_id}")


@app.command()
def sync():
    """Queue a channel refresh, deduplicated within the current sync interval."""
    with session_factory().begin() as session:
        job = enqueue_sync(session, settings())
        typer.echo(f"job_id={job.id} state={job.state}")


@app.command("worker")
def run_worker(once: bool = False):
    """Run restartable jobs and transactional indexing outbox."""
    cfg, factory, index = services()
    worker(factory, cfg, index, once=once)


@app.command()
def export():
    """Regenerate active Markdown exports from canonical data."""
    with session_factory()() as session:
        export_all(session, settings().export_dir)


@app.command("reconcile")
def reconcile_indexes():
    """Repair missing points and retire inactive-version projections."""
    cfg, factory, index = services()
    reconcile(factory, cfg, index)
    typer.echo("Eventi di riparazione accodati.")


@app.command()
def withdraw(document_id: str):
    """Withdraw a document while preserving history and other sources' evidence."""
    with session_factory().begin() as session:
        withdraw_document(session, uuid.UUID(document_id), settings())


@app.command()
def retry(job_id: str):
    """Explicitly retry a failed, blocked, or completed acquisition job."""
    with session_factory().begin() as session:
        retry_job(session, uuid.UUID(job_id))


@app.command("retry-index")
def retry_index(event_id: str):
    with session_factory().begin() as session:
        event = session.get(Outbox, uuid.UUID(event_id), with_for_update=True)
        if event is None:
            raise typer.BadParameter("Unknown event")
        event.state, event.attempts, event.next_attempt_at = "pending", 0, now()


@app.command()
def ask(question: str):
    """Retrieve active canonical passages and, optionally, generate a cited answer."""
    _, factory, index = services()
    with factory() as session:
        citations = retrieve(session, index, question)
    typer.echo(index.provider.answer(question, citations))
    for i, citation in enumerate(citations, 1):
        typer.echo(
            f"[{i}] {citation['title']} — {citation['url']}\n"
            f"document_id={citation['document_id']} version_id={citation['version_id']}"
        )


@app.command()
def status():
    with session_factory()() as session:
        for model in (Job, Outbox):
            for item in session.scalars(select(model)):
                typer.echo(
                    f"{model.__tablename__} {item.id} {item.state} attempts={item.attempts} {item.last_error or ''}"
                )


if __name__ == "__main__":
    app()
