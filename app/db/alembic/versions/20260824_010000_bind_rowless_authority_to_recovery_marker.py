"""bind rowless recovery authorities to durable recovery markers

Revision ID: 20260824_010000_bind_rowless_authority_to_recovery_marker
Revises: 20260824_000000_add_http_bridge_rowless_recovery_authorities
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260824_010000_bind_rowless_authority_to_recovery_marker"
down_revision = "20260824_000000_add_http_bridge_rowless_recovery_authorities"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_rowless_recovery_authorities"
_COLUMN = "origin_marker_session_id"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def _marker_authority_count(connection: Connection) -> int:
    return int(connection.execute(sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL")).scalar_one())


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if _COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(36), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if _COLUMN not in columns:
        return
    if _marker_authority_count(bind) > 0:
        raise RuntimeError("rowless marker-authority downgrade refused: durable marker replay fences exist")
    op.drop_column(_TABLE, _COLUMN)
