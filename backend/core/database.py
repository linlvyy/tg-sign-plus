from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import get_settings
from backend.utils.env import read_int_env

Base = declarative_base()

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def init_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None and _SessionLocal is not None:
        return

    settings = get_settings()
    db_url = settings.database_url
    kwargs: dict = {"echo": False}

    if _is_sqlite(db_url):
        timeout = read_int_env("APP_DB_SQLITE_TIMEOUT", 30, minimum=1)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": timeout}
    else:
        kwargs.update(
            {
                "pool_pre_ping": True,
                "pool_recycle": read_int_env("APP_DB_POOL_RECYCLE", 1800, minimum=1),
                "pool_timeout": read_int_env("APP_DB_POOL_TIMEOUT", 30, minimum=1),
                "pool_size": read_int_env("APP_DB_POOL_SIZE", 5, minimum=1),
                "max_overflow": read_int_env("APP_DB_MAX_OVERFLOW", 10, minimum=0),
                "pool_use_lifo": True,
            }
        )

    engine = create_engine(db_url, **kwargs)

    if _is_sqlite(db_url):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    else:
        @event.listens_for(engine, "connect")
        def set_postgres_session_timezone(dbapi_connection, connection_record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET TIME ZONE UTC")

        @event.listens_for(engine, "checkout")
        def check_postgres_connection(dbapi_connection, connection_record, connection_proxy):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SELECT 1")
            finally:
                cursor.close()

    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    return _engine  # type: ignore[return-value]


def get_session_local() -> sessionmaker:
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal  # type: ignore[return-value]


def get_db():
    session_local = get_session_local()
    db = session_local()
    try:
        yield db
    finally:
        db.close()
