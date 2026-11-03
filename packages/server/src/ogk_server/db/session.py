from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ogk_server.db.models import Base


def create_database(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        connection.isolation_level = None
        cursor = connection.cursor()
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            raise RuntimeError("SQLite WAL could not be enabled")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection):
        # Serialize write requests BEFORE their first read, avoiding WAL snapshot
        # upgrade races and double consumption/rotation across worker processes.
        mode = connection.get_execution_options().get("sqlite_transaction_mode", "DEFERRED")
        connection.exec_driver_sql("BEGIN IMMEDIATE" if mode == "IMMEDIATE" else "BEGIN")

    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize_database(engine) -> None:
    Base.metadata.create_all(engine)


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            session.connection(execution_options={"sqlite_transaction_mode": "IMMEDIATE"})
        # Services explicitly commit successes and security state on rejection.
        # Any unfinished transaction is rolled back on close.
        yield session
