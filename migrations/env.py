import os

from alembic import context
from sqlalchemy import create_engine, pool

from virtualqueue.models import Base

url = os.getenv("DATABASE_URL", context.config.get_main_option("sqlalchemy.url"))
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=pool.NullPool).connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
