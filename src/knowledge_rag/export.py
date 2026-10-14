import os
import tempfile
from pathlib import Path

import yaml
from sqlalchemy import select

from knowledge_rag.models import Document, DocumentLink, Version


def markdown(session, document, version):
    metadata = {
        **version.metadata_json,
        "document_id": str(document.id),
        "version_id": str(version.id),
        "source_type": document.source_type,
        "title": version.metadata_json.get("source_title", document.title),
        "url": version.metadata_json.get("source_url", document.url),
        "ingested_at": version.created_at.isoformat(),
    }
    header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    references = session.scalars(
        select(DocumentLink).where(DocumentLink.source_document_id == document.id)
    ).all()
    links = "\n".join(f"- {link.encountered_url} ({link.status})" for link in references)
    return (
        f"---\n{header}---\n\n# {metadata['title']}\n\n"
        f"## Riferimenti\n{links or 'Nessuno'}\n\n"
        f"## Testo normalizzato e corretto\n\n{version.corrected_text}\n"
    )


def export_all(session, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    current_files = set()
    for document in session.scalars(
        select(Document).where(Document.enabled.is_(True), Document.active_version_id.is_not(None))
    ):
        version = session.get(Version, document.active_version_id)
        filename = f"{document.id}.md"
        current_files.add(filename)
        content = markdown(session, document, version)
        target = directory / filename
        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue
        descriptor, temp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(temp, target)
        finally:
            Path(temp).unlink(missing_ok=True)
    # Retire only managed UUID filenames; never delete user-created notes.
    import uuid

    for path in directory.glob("*.md"):
        try:
            uuid.UUID(path.stem)
        except ValueError:
            continue
        if path.name not in current_files:
            path.unlink()
