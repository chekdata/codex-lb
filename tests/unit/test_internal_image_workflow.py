from __future__ import annotations

from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "internal-image.yml"


def test_internal_image_verifies_and_dispatches_exact_multiarch_receipt() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "id: build" in workflow
    assert "Verify immutable image receipt" in workflow
    assert "EXPECTED_DIGEST: ${{ steps.build.outputs.digest }}" in workflow
    assert "Platform:[[:space:]]*linux/amd64" in workflow
    assert "Platform:[[:space:]]*linux/arm64" in workflow
    assert "codex-lb-image-published" in workflow
    assert "peter-evans/repository-dispatch@ff45666b9427631e3450c54a1bcbee4d9ff4d7c0" in workflow
    assert "token: ${{ secrets.GH_PAT }}" in workflow
    assert "secrets.GH_PAT ||" not in workflow
    assert '"source_sha":"${{ github.sha }}"' in workflow
    assert '"source_digest":"${{ steps.build.outputs.digest }}"' in workflow
