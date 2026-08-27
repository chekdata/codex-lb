"""merge upstream v1.24.0 and CHEK compatibility heads

Revision ID: 20260826_010000_merge_v1240_and_chek_heads
Revises: 20260816_000000_add_model_source_embeddings, 20260826_000000_add_response_transition_manifest
Create Date: 2026-08-26
"""

from __future__ import annotations

revision = "20260826_010000_merge_v1240_and_chek_heads"
down_revision = (
    "20260816_000000_add_model_source_embeddings",
    "20260826_000000_add_response_transition_manifest",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two already-applied migration lineages."""


def downgrade() -> None:
    """Split back to the two parent migration heads."""
