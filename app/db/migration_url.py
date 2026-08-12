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


def normalize_postgres_schema(schema: str | None) -> str | None:
    if schema is None:
        return None
    normalized = schema.strip()
    if not normalized:
        return None
    return normalized


def quote_postgres_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def postgres_search_path(schema: str | None) -> str | None:
    normalized = normalize_postgres_schema(schema)
    if normalized is None:
        return None
    quoted = quote_postgres_identifier(normalized)
    if normalized == "public":
        return quoted
    return f"{quoted},public"


def postgres_migration_search_path(schema: str | None) -> str | None:
    normalized = normalize_postgres_schema(schema)
    if normalized is None:
        return None
    return quote_postgres_identifier(normalized)


def postgres_qualified_name(identifier: str, schema: str | None) -> str:
    normalized = normalize_postgres_schema(schema)
    quoted_identifier = quote_postgres_identifier(identifier)
    if normalized is None:
        return quoted_identifier
    return f"{quote_postgres_identifier(normalized)}.{quoted_identifier}"


def ensure_postgres_schema_exists(connection: Connection, schema: str | None) -> None:
    if schema is None or connection.dialect.name != "postgresql":
        return

    normalized = normalize_postgres_schema(schema)
    if normalized is None:
        return
    if normalized == "public":
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


def apply_postgres_migration_search_path(connection: Connection, schema: str | None) -> None:
    normalized = normalize_postgres_schema(schema)
    search_path = postgres_migration_search_path(normalized)
    if search_path is None or connection.dialect.name != "postgresql":
        return
    connection.execute(
        text("SELECT set_config('search_path', :search_path, false)"),
        {"search_path": search_path},
    )
    # Reflection caches PostgreSQL's default schema before this deployment-
    # specific search path is applied. Keep it aligned with unqualified DDL.
    connection.dialect.default_schema_name = normalized
