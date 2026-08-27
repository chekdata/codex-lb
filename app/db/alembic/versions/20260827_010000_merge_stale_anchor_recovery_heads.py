"""merge stale-anchor recovery and CHEK repair heads

Revision ID: 20260827_010000_merge_stale_anchor_recovery_heads
Revises: 20260821_000000_add_retry_circuit_admission_generation,
    20260827_000000_repair_missing_http_bridge_tables
Create Date: 2026-08-27
"""

from __future__ import annotations

revision = "20260827_010000_merge_stale_anchor_recovery_heads"
down_revision = (
    "20260821_000000_add_retry_circuit_admission_generation",
    "20260827_000000_repair_missing_http_bridge_tables",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
