"""add rowless HTTP bridge recovery authorities

Revision ID: 20260824_000000_add_http_bridge_rowless_recovery_authorities
Revises: 20260823_000000_add_http_bridge_recovery_required_marker
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260824_000000_add_http_bridge_rowless_recovery_authorities"
down_revision = "20260823_000000_add_http_bridge_recovery_required_marker"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_rowless_recovery_authorities"
_STATE = sa.Enum(
    "captured",
    "approved",
    "unknown",
    "consumed",
    name="http_bridge_rowless_recovery_state",
)


def _has_table(connection: Connection) -> bool:
    return sa.inspect(connection).has_table(_TABLE)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("api_key_scope", sa.String(255), nullable=False),
        sa.Column("session_key_kind", sa.String(64), nullable=False),
        sa.Column("strong_session_hash", sa.String(64), nullable=False),
        sa.Column("stale_anchor_hash", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("generation_nonce", sa.String(64), nullable=False),
        sa.Column("state", _STATE, nullable=False, server_default="captured"),
        sa.Column("captured_input_item_count", sa.Integer(), nullable=False),
        sa.Column("captured_input_fingerprint", sa.String(64), nullable=False),
        sa.Column("non_input_contract_fingerprint", sa.String(64), nullable=False),
        sa.Column("settled_direct_call_ledger_digest", sa.String(64), nullable=False),
        sa.Column("projected_payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("actual_wire_fingerprint", sa.String(64), nullable=False),
        sa.Column("settled_direct_call_unresolved_count", sa.Integer(), nullable=False),
        sa.Column("selected_account_intent", sa.String(), nullable=False),
        sa.Column("captured_task_identity_hash", sa.String(64), nullable=False),
        sa.Column("captured_session_identity_hash", sa.String(64), nullable=False),
        sa.Column("captured_task_authority_digest", sa.String(64), nullable=False),
        sa.Column("request_self_contained", sa.Boolean(), nullable=False),
        sa.Column("request_account_neutral", sa.Boolean(), nullable=False),
        sa.Column("challenge_nonce_hash", sa.String(64), nullable=True),
        sa.Column("challenge_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_receipt_sha256", sa.String(64), nullable=True),
        sa.Column("checkpoint_jsonl_sha256", sa.String(64), nullable=True),
        sa.Column("checkpoint_jsonl_size_bytes", sa.Integer(), nullable=True),
        sa.Column("checkpoint_jsonl_last_offset", sa.Integer(), nullable=True),
        sa.Column("checkpoint_task_identity_hash", sa.String(64), nullable=True),
        sa.Column("checkpoint_session_identity_hash", sa.String(64), nullable=True),
        sa.Column("checkpoint_strong_session_hash", sa.String(64), nullable=True),
        sa.Column("checkpoint_task_authority_digest", sa.String(64), nullable=True),
        sa.Column("checkpoint_tool_ledger_digest", sa.String(64), nullable=True),
        sa.Column("approved_by_actor", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wire_request_fingerprint", sa.String(64), nullable=True),
        sa.Column("replacement_session_id", sa.String(36), nullable=True),
        sa.Column("dispatch_request_id", sa.String(255), nullable=True),
        sa.Column("dispatch_send_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_response_id_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "api_key_scope",
            "strong_session_hash",
            "stale_anchor_hash",
            name="uq_http_bridge_rowless_recovery_authority",
        ),
        sa.UniqueConstraint(
            "api_key_scope",
            "strong_session_hash",
            "captured_input_fingerprint",
            "non_input_contract_fingerprint",
            "settled_direct_call_ledger_digest",
            "projected_payload_fingerprint",
            name="uq_http_bridge_rowless_recovery_task_contract",
        ),
    )
    op.create_index("idx_http_bridge_rowless_recovery_state", _TABLE, ["state", "updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    replay_fences = bind.execute(sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE state <> 'captured'")).scalar_one()
    if int(replay_fences) > 0:
        raise RuntimeError("rowless recovery downgrade refused: approved/unknown/consumed replay fences exist")
    op.drop_index("idx_http_bridge_rowless_recovery_state", table_name=_TABLE)
    op.drop_table(_TABLE)
    _STATE.drop(bind, checkfirst=True)
