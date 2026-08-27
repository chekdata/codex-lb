"""repair durable HTTP bridge tables missing from an ahead-of-head schema

Revision ID: 20260827_000000_repair_missing_http_bridge_tables
Revises: 20260826_010000_merge_v1240_and_chek_heads
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.models import Base

revision = "20260827_000000_repair_missing_http_bridge_tables"
down_revision = "20260826_010000_merge_v1240_and_chek_heads"
branch_labels = None
depends_on = None


# Some legacy databases were stamped at Alembic head without physically
# applying the HTTP bridge lineage. Recreate only the durable bridge tables;
# existing tables and rows are left untouched.
_TABLES_IN_FOREIGN_KEY_ORDER = (
    "http_bridge_sessions",
    "http_bridge_session_aliases",
    "http_bridge_retry_circuits",
    "http_bridge_recovery_attempts",
    "http_bridge_operations",
    "http_bridge_operation_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in _TABLES_IN_FOREIGN_KEY_ORDER:
        if inspector.has_table(table_name):
            continue
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
        inspector = sa.inspect(bind)


def downgrade() -> None:
    # This migration repairs an existing production schema. It intentionally
    # does not drop tables or data during a downgrade.
    pass
