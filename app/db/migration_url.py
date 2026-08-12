from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.schema import CreateSchema

from app.db.sqlite_utils import normalize_sqlite_url


def to_sync_database_url(database_url: str) -> str:
    database_url = normalize_sqlite_url(database_url)
    parsed = make_url(database_url)
    driver = parsed.drivername

    if driver == "sqlite+aiosqlite":
        parsed = parsed.set(drivername="sqlite")
    elif driver == "postgresql+asyncpg":
        parsed = parsed.set(drivername="postgresql+psycopg")

    return parsed.render_as_string(hide_password=False)


def postgres_search_path(schema: str | None) -> str | None:
    if schema is None:
        return None
    normalized = schema.strip()
    if not normalized:
        return None
    if normalized.casefold() == "public":
        return "public"
    return f"{normalized},public"


def ensure_postgres_schema_exists(connection: Connection, schema: str | None) -> None:
    if schema is None or connection.dialect.name != "postgresql":
        return

    normalized = schema.strip()
    if not normalized or normalized.casefold() == "public":
        return

    connection.execute(CreateSchema(normalized, if_not_exists=True))


def apply_postgres_search_path(connection: Connection, schema: str | None) -> None:
    search_path = postgres_search_path(schema)
    if search_path is None or connection.dialect.name != "postgresql":
        return
    connection.execute(
        text("SELECT set_config('search_path', :search_path, false)"),
        {"search_path": search_path},
    )
