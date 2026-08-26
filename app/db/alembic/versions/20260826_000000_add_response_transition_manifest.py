"""add durable response transition manifest

Revision ID: 20260826_000000_add_response_transition_manifest
Revises: 20260825_000000_add_rowless_authorization_provenance
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260826_000000_add_response_transition_manifest"
down_revision = "20260825_000000_add_rowless_authorization_provenance"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_sessions"
_COLUMN = "latest_response_transition_manifest_json"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def _active_manifest_exists(connection: Connection) -> bool:
    return (
        connection.execute(sa.text(f"SELECT 1 FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL LIMIT 1")).first() is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    if _active_manifest_exists(bind):
        raise RuntimeError(
            "Cannot downgrade while durable response transition manifests exist; "
            "retire the manifest-backed checkpoints first."
        )
    op.drop_column(_TABLE, _COLUMN)
