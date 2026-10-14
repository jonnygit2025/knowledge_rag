import re

from sqlalchemy import select
from sqlalchemy.orm import aliased

from knowledge_rag.models import (
    Chunk,
    Document,
    Entity,
    Evidence,
    Graph,
    Mention,
    Relation,
    Version,
)

CATALOG = {
    "PostgreSQL": "https://www.postgresql.org/",
    "Qdrant": "https://qdrant.tech/",
    "GitHub": "https://github.com/",
    "LightRAG": "https://github.com/HKUDS/LightRAG",
}


def entity(session, key, name, kind="technology"):
    value = session.scalar(select(Entity).where(Entity.identity_key == key))
    if value is None:
        value = Entity(identity_key=key, canonical_name=name, entity_type=kind)
        session.add(value)
        session.flush()
    return value


def build_graph(session, document, version, chunks, assertions):
    graph = Graph(version_id=version.id)
    session.add(graph)
    session.flush()
    source = entity(session, f"document:{document.id}", document.title, "document")
    for chunk in chunks:
        for name, key in CATALOG.items():
            if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", chunk.text, re.I):
                target = entity(session, key, name)
                session.add(
                    Mention(
                        graph_id=graph.id, entity_id=target.id, chunk_id=chunk.id, quote=chunk.text
                    )
                )
                add_relation(session, graph, source, "mentions", target, chunk, chunk.text)
    for item in assertions:
        # Structured imports/fixtures only. An extracted assertion without a verbatim quote is rejected.
        quote = item["quote"]
        chunk = next((c for c in chunks if quote and quote in c.text), None)
        if chunk is None:
            raise ValueError("Graph assertion quote must occur verbatim inside a canonical chunk")
        subject = entity(session, item["subject_key"], item["subject"])
        target = entity(session, item["object_key"], item["object"])
        add_relation(session, graph, subject, item["predicate"], target, chunk, quote)


def add_relation(session, graph, subject, predicate, target, chunk, quote):
    relation = session.scalar(
        select(Relation).where(
            Relation.subject_entity_id == subject.id,
            Relation.predicate == predicate,
            Relation.object_entity_id == target.id,
        )
    )
    if relation is None:
        relation = Relation(
            subject_entity_id=subject.id, predicate=predicate, object_entity_id=target.id
        )
        session.add(relation)
        session.flush()
    session.add(
        Evidence(relation_id=relation.id, graph_id=graph.id, chunk_id=chunk.id, quote=quote)
    )


def graph_rows(session, document_id=None, version_id=None):
    subject, target = aliased(Entity), aliased(Entity)
    query = (
        select(
            subject.canonical_name,
            Relation.predicate,
            target.canonical_name,
            Evidence.quote,
            Evidence.chunk_id,
            Document.id,
            Graph.version_id,
        )
        .select_from(Evidence)
        .join(Relation)
        .join(Graph)
        .join(Chunk)
        .join(Document, Document.active_version_id == Graph.version_id)
        .join(subject, subject.id == Relation.subject_entity_id)
        .join(target, target.id == Relation.object_entity_id)
        .where(Document.enabled.is_(True))
    )
    if document_id:
        query = query.where(Document.id == document_id)
    if version_id:
        # Historical version inspection is explicit, never used in retrieval.
        query = (
            select(
                subject.canonical_name,
                Relation.predicate,
                target.canonical_name,
                Evidence.quote,
                Evidence.chunk_id,
                Document.id,
                Graph.version_id,
            )
            .select_from(Evidence)
            .join(Relation)
            .join(Graph)
            .join(Version, Version.id == Graph.version_id)
            .join(Document, Document.id == Version.document_id)
            .join(subject, subject.id == Relation.subject_entity_id)
            .join(target, target.id == Relation.object_entity_id)
            .where(Graph.version_id == version_id)
        )
        if document_id:
            query = query.where(Document.id == document_id)
    return session.execute(query).all()
