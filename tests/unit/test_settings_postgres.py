from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config.settings import Settings


def test_database_postgres_schema_accepts_postgres_identifier_byte_limit() -> None:
    schema = "a" * 63

    settings = Settings(_env_file=None, database_postgres_schema=schema)

    assert settings.database_postgres_schema == schema


@pytest.mark.parametrize("schema", ["a" * 64, "数" * 22])
def test_database_postgres_schema_rejects_more_than_63_utf8_bytes(schema: str) -> None:
    with pytest.raises(ValidationError, match="at most 63 UTF-8 bytes"):
        Settings(_env_file=None, database_postgres_schema=schema)
