"""add durable HTTP bridge rejected-anchor recovery marker

Revision ID: 20260823_000000_add_http_bridge_recovery_required_marker
Revises: 20260811_000000_add_hourly_rollup_cancelled_count
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260823_000000_add_http_bridge_recovery_required_marker"
down_revision = "20260811_000000_add_hourly_rollup_cancelled_count"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_sessions"
_COLUMNS = (
    "recovery_required_anchor_hash",
    "recovery_required_account_id",
    "recovery_required_attempt_fingerprint",
    "recovery_required_at",
)


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch_op:
        if _COLUMNS[0] not in existing:
            batch_op.add_column(sa.Column(_COLUMNS[0], sa.String(length=64), nullable=True))
        if _COLUMNS[1] not in existing:
            batch_op.add_column(sa.Column(_COLUMNS[1], sa.String(), nullable=True))
        if _COLUMNS[2] not in existing:
            batch_op.add_column(sa.Column(_COLUMNS[2], sa.String(length=64), nullable=True))
        if _COLUMNS[3] not in existing:
            batch_op.add_column(sa.Column(_COLUMNS[3], sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch_op:
        for column in reversed(_COLUMNS):
            if column in existing:
                batch_op.drop_column(column)
