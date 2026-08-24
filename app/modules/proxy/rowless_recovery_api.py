"""Authenticated dashboard control plane for rowless semantic rebases."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth.dashboard_access import DashboardPrincipal
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dependencies import require_dashboard_admin_access, set_dashboard_error_format
from app.db.session import SessionLocal
from app.modules.proxy.rowless_recovery import canonical_json_sha256
from app.modules.proxy.rowless_recovery_repository import (
    RowlessCheckpointReceipt,
    RowlessRecoveryRepository,
    RowlessRecoveryStateError,
)

router = APIRouter(
    prefix="/api/http-bridge/rowless-recovery",
    tags=["dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
)


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=1)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int = Field(ge=1)
    acknowledgement: Literal["operator_acknowledged_semantic_rebase"]
    challenge: str = Field(min_length=32, max_length=512)
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt: RowlessCheckpointReceipt


async def require_authenticated_rebase_admin(request: Request) -> DashboardPrincipal:
    principal = await require_dashboard_admin_access(request)
    if principal.auth_mode != DashboardAuthMode.TRUSTED_HEADER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A trusted proxy operator is required for semantic rebase approval",
        )
    if not (principal.actor or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A trusted proxy operator identity is required for semantic rebase approval",
        )
    return principal


@router.get("")
async def list_rowless_authorities(
    principal: DashboardPrincipal = Depends(require_authenticated_rebase_admin),
) -> list[dict[str, object]]:
    del principal
    async with SessionLocal() as session:
        authorities = await RowlessRecoveryRepository(session).list_authorities()
    return [
        {
            "id": item.id,
            "state": item.state.value,
            "generation": item.generation,
            "strongSessionHash": item.strong_session_hash,
            "taskAuthorityDigest": item.captured_task_authority_digest,
            "staleAnchorHash": item.stale_anchor_hash,
            "markerOriginBound": item.origin_marker_session_id is not None,
            "inputItemCount": item.captured_input_item_count,
            "createdAt": item.created_at,
            "updatedAt": item.updated_at,
        }
        for item in authorities
    ]


@router.get("/status")
async def rowless_recovery_status(
    principal: DashboardPrincipal = Depends(require_authenticated_rebase_admin),
) -> dict[str, object]:
    del principal
    async with SessionLocal() as session:
        repository = RowlessRecoveryRepository(session)
        counts = await repository.authority_state_counts()
        marker_bound_counts = await repository.authority_state_counts(marker_bound_only=True)
    replay_fence_count = counts["approved"] + counts["unknown"] + counts["consumed"]
    marker_bound_count = sum(marker_bound_counts.values())
    return {
        "stateCounts": counts,
        "markerBoundStateCounts": marker_bound_counts,
        "replayFenceCount": replay_fence_count,
        "markerBoundAuthorityCount": marker_bound_count,
        "preRowlessImageCompatible": replay_fence_count == 0 and marker_bound_count == 0,
        "preMarkerRecoveryImageCompatible": marker_bound_count == 0,
        "minimumRollbackCapability": (
            "rowless_marker_recovery_v2"
            if marker_bound_count
            else ("rowless_recovery_v1" if replay_fence_count else None)
        ),
    }


@router.post("/{authority_id}/challenge")
async def issue_rowless_challenge(
    authority_id: str,
    body: ChallengeRequest,
    principal: DashboardPrincipal = Depends(require_authenticated_rebase_admin),
) -> dict[str, str | int | bool | datetime]:
    del principal
    try:
        async with SessionLocal() as session:
            challenge = await RowlessRecoveryRepository(session).issue_challenge(
                authority_id=authority_id,
                generation=body.generation,
            )
    except RowlessRecoveryStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "authorityId": challenge.authority.id,
        "generation": challenge.authority.generation,
        "challenge": challenge.challenge,
        "expiresAt": challenge.expires_at,
        "strongSessionHash": challenge.authority.strong_session_hash,
        "taskAuthorityDigest": challenge.authority.captured_task_authority_digest,
        "capturedInputItemCount": challenge.authority.captured_input_item_count,
        "capturedInputFingerprint": challenge.authority.captured_input_fingerprint,
        "nonInputContractFingerprint": challenge.authority.non_input_contract_fingerprint,
        "retainedRequestLedgerDigest": challenge.authority.settled_direct_call_ledger_digest,
        "projectedPayloadFingerprint": challenge.authority.projected_payload_fingerprint,
        "actualWireFingerprint": challenge.authority.actual_wire_fingerprint,
        "retainedUnresolvedCount": challenge.authority.settled_direct_call_unresolved_count,
        "requestSelfContained": challenge.authority.request_self_contained,
        "requestAccountNeutral": challenge.authority.request_account_neutral,
        "selectedAccountIntentHash": canonical_json_sha256(challenge.authority.selected_account_intent),
    }


@router.post("/{authority_id}/approve")
async def approve_rowless_recovery(
    authority_id: str,
    body: ApproveRequest,
    request: Request,
    principal: DashboardPrincipal = Depends(require_authenticated_rebase_admin),
) -> dict[str, str | int]:
    actor = principal.actor or "verified_standard_dashboard_admin"
    try:
        async with SessionLocal() as session:
            approved = await RowlessRecoveryRepository(session).approve(
                authority_id=authority_id,
                generation=body.generation,
                challenge=body.challenge,
                declared_receipt_sha256=body.receipt_sha256,
                receipt=body.receipt,
                acknowledgement=body.acknowledgement,
                approved_actor=actor,
                request_id=getattr(request.state, "request_id", None),
            )
    except RowlessRecoveryStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"authorityId": approved.id, "generation": approved.generation, "state": approved.state.value}
