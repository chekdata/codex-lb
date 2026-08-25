"""add rowless automatic authorization provenance

Revision ID: 20260825_000000_add_rowless_authorization_provenance
Revises: 20260824_010000_bind_rowless_authority_to_recovery_marker
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260825_000000_add_rowless_authorization_provenance"
down_revision = "20260824_010000_bind_rowless_authority_to_recovery_marker"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_rowless_recovery_authorities"
_SESSION_TABLE = "http_bridge_sessions"
_MODE_COLUMN = "authorization_mode"
_PROOF_COLUMN = "authorization_proof_sha256"
_MARKER_REQUEST_COLUMN = "recovery_required_attempt_request_id"


def _columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE) if column.get("name") is not None}


def _session_columns(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_SESSION_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_SESSION_TABLE) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if _MODE_COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_MODE_COLUMN, sa.String(32), nullable=True))
    if _PROOF_COLUMN not in columns:
        op.add_column(_TABLE, sa.Column(_PROOF_COLUMN, sa.String(64), nullable=True))
    if _MARKER_REQUEST_COLUMN not in _session_columns(bind):
        op.add_column(_SESSION_TABLE, sa.Column(_MARKER_REQUEST_COLUMN, sa.String(255), nullable=True))
    # Preserve pre-upgrade claims that already reached the durable journal;
    # they remain fail-closed and inherit that exact request owner. Claims in
    # the old claim-before-journal crash window are physically unsent and can
    # be released safely.
    op.execute(
        sa.text(
            f"UPDATE {_SESSION_TABLE} SET {_MARKER_REQUEST_COLUMN} = ("
            "SELECT request_id FROM http_bridge_recovery_attempts "
            f"WHERE session_id = {_SESSION_TABLE}.id "
            f"AND request_fingerprint = {_SESSION_TABLE}.recovery_required_attempt_fingerprint LIMIT 1"
            ") WHERE recovery_required_attempt_fingerprint IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE {_SESSION_TABLE} SET recovery_required_attempt_fingerprint = NULL "
            f"WHERE recovery_required_attempt_fingerprint IS NOT NULL AND {_MARKER_REQUEST_COLUMN} IS NULL"
        )
    )
    op.execute(
        sa.text(
            f"UPDATE {_TABLE} "
            f"SET {_MODE_COLUMN} = 'operator_checkpoint', "
            f"{_PROOF_COLUMN} = checkpoint_receipt_sha256 "
            "WHERE checkpoint_receipt_sha256 IS NOT NULL "
            f"AND {_MODE_COLUMN} IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if _MODE_COLUMN in columns:
        active_automatic_authority = bind.execute(
            sa.text(
                f"SELECT 1 FROM {_TABLE} "
                f"WHERE {_MODE_COLUMN} = 'automatic_live_request' "
                "AND state IN ('approved', 'unknown') LIMIT 1"
            )
        ).first()
        if active_automatic_authority is not None:
            raise RuntimeError(
                "Cannot downgrade while active automatic rowless recovery authorities exist; "
                "complete or safely retire them first."
            )
    if _PROOF_COLUMN in columns:
        op.drop_column(_TABLE, _PROOF_COLUMN)
    if _MODE_COLUMN in columns:
        op.drop_column(_TABLE, _MODE_COLUMN)
    if _MARKER_REQUEST_COLUMN in _session_columns(bind):
        op.drop_column(_SESSION_TABLE, _MARKER_REQUEST_COLUMN)
