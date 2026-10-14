import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from knowledge_rag.jobs import load_demo, pump_outbox
from knowledge_rag.models import Document
from knowledge_rag.providers import Providers
from knowledge_rag.retrieval import retrieve
from knowledge_rag.vectors import VectorIndex


@pytest.mark.integration
def test_migrated_postgres_real_qdrant_and_immutability(cfg):
    url, qdrant_url = os.getenv("TEST_DATABASE_URL"), os.getenv("TEST_QDRANT_URL")
    if not url or not qdrant_url:
        pytest.skip("Requires PostgreSQL and Qdrant servers")
    schema = "kr_integration_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    cfg = cfg.model_copy(update={"chunk_chars": 300 + uuid.uuid4().int % 500})
    factory = sessionmaker(engine, expire_on_commit=False)
    client = QdrantClient(url=qdrant_url)
    index = VectorIndex(cfg, Providers(cfg, factory), client)
    try:
        with engine.begin() as connection:
            alembic = Config("alembic.ini")
            alembic.attributes["connection"] = connection
            command.upgrade(alembic, "head")
        load_demo(factory, cfg)
        while pump_outbox(factory, cfg, index):
            pass
        with factory() as session:
            citations = retrieve(session, index, "Qdrant indicizza i chunk?")
            assert citations
            assert all(
                session.get(Document, uuid.UUID(c["document_id"])).active_version_id
                == uuid.UUID(c["version_id"])
                for c in citations
            )
        with pytest.raises(Exception, match="immutable"), engine.begin() as connection:
            connection.execute(text("UPDATE document_versions SET raw_text='tampered'"))
        with pytest.raises(Exception, match="belong"), engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE documents SET active_version_id = (
                    SELECT v.id FROM document_versions v WHERE v.document_id <> documents.id LIMIT 1
                )
            """)
            )
    finally:
        if client.collection_exists(cfg.namespace):
            client.delete_collection(cfg.namespace)
        client.close()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
