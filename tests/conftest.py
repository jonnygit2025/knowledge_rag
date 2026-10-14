import os
import uuid

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from knowledge_rag.config import Settings
from knowledge_rag.models import Base
from knowledge_rag.providers import Providers
from knowledge_rag.vectors import VectorIndex


@pytest.fixture(params=["sqlite", "postgres"])
def factory(request):
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def foreign_keys(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        yield sessionmaker(engine, expire_on_commit=False)
        engine.dispose()
    else:
        url = os.getenv("TEST_DATABASE_URL")
        if not url:
            pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL integration tests")
        schema = "kr_test_" + uuid.uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(engine)
        try:
            yield sessionmaker(engine, expire_on_commit=False)
        finally:
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


@pytest.fixture()
def cfg(tmp_path):
    return Settings(
        _env_file=None,
        startup_sync=False,
        config_file=tmp_path / "config.yaml",
        export_dir=tmp_path / "exports",
        import_dir=tmp_path / "imports",
    )


@pytest.fixture()
def index(cfg, factory):
    client = QdrantClient(":memory:")
    yield VectorIndex(cfg, Providers(cfg, factory), client=client)
    client.close()
