from contextlib import contextmanager

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from .models import Service


class Database:
    def __init__(self, url):
        args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=args, pool_pre_ping=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def transaction(self, service_id=None):
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                if self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    session.begin()
                if service_id:
                    session.execute(select(Service).where(Service.id == service_id).with_for_update()).scalar_one_or_none()
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    @contextmanager
    def read(self):
        with Session(self.engine) as session:
            yield session
