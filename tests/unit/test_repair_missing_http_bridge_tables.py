from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "app/db/alembic/versions/20260827_000000_repair_missing_http_bridge_tables.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("repair_missing_http_bridge_tables", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repair_creates_missing_bridge_tables_idempotently() -> None:
    from app.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        Base.metadata.tables["accounts"].create(connection)
        migration = _load_migration()
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
            migration.upgrade()

        table_names = set(inspect(connection).get_table_names())

    assert set(migration._TABLES_IN_FOREIGN_KEY_ORDER).issubset(table_names)
