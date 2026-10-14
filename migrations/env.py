from alembic import context
from sqlalchemy import create_engine, pool

from knowledge_rag.config import settings
from knowledge_rag.models import Base

config = context.config
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=settings().database_url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:

    def run(connection):
        if connection.dialect.name != "postgresql":
            raise RuntimeError("Production migrations require PostgreSQL")
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

    supplied = config.attributes.get("connection")
    if supplied is not None:
        run(supplied)
    else:
        engine = create_engine(settings().database_url, poolclass=pool.NullPool)
        with engine.connect() as connection:
            run(connection)
