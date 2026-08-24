"""Durable lifecycle for an operator-acknowledged rowless semantic rebase."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.proxy_websocket import UPSTREAM_WEBSOCKET_CLOSED_BEFORE_SEND_CODE
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import (
    AuditLog,
    HttpBridgeRecoveryAttemptRecord,
    HttpBridgeRecoveryAttemptState,
    HttpBridgeRowlessRecoveryAuthority,
    HttpBridgeRowlessRecoveryState,
    HttpBridgeSessionRecord,
)
from app.db.session import sqlite_writer_section
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeRepository,
    _encode_pending_tool_calls,
    durable_bridge_hash,
)
from app.modules.proxy.rowless_recovery import (
    ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
    RowlessRecoveryCaptureFacts,
    canonical_json_sha256,
)

ROWLESS_RECOVERY_CHALLENGE_TTL_SECONDS = 900
ROWLESS_RECOVERY_CAPTURED_RETENTION_SECONDS = 7 * 24 * 3600
ROWLESS_RECOVERY_MAX_CAPTURED_PER_API_SCOPE = 100


class RowlessRecoveryConflictError(RuntimeError):
    """The durable authority exists but does not bind the same contract."""


class RowlessRecoveryStateError(RuntimeError):
    """A fail-closed lifecycle precondition did not match."""


@dataclass(frozen=True, slots=True)
class RowlessRecoveryAuthoritySnapshot:
    id: str
    api_key_scope: str
    session_key_kind: str
    strong_session_hash: str
    stale_anchor_hash: str
    generation: int
    generation_nonce: str
    state: HttpBridgeRowlessRecoveryState
    captured_input_item_count: int
    captured_input_fingerprint: str
    non_input_contract_fingerprint: str
    settled_direct_call_ledger_digest: str
    projected_payload_fingerprint: str
    actual_wire_fingerprint: str
    origin_marker_session_id: str | None
    settled_direct_call_unresolved_count: int
    selected_account_intent: str
    captured_task_identity_hash: str
    captured_session_identity_hash: str
    captured_task_authority_digest: str
    request_self_contained: bool
    request_account_neutral: bool
    checkpoint_receipt_sha256: str | None
    replacement_session_id: str | None
    dispatch_request_id: str | None
    dispatch_send_started_at: datetime | None
    wire_request_fingerprint: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RowlessRecoveryChallenge:
    authority: RowlessRecoveryAuthoritySnapshot
    challenge: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RowlessCheckpointReceipt:
    schema: str
    remote_session_jsonl_sha256: str
    remote_session_jsonl_size_bytes: int
    remote_session_jsonl_last_offset: int
    full_checkpoint_tool_ledger_digest: str
    unresolved_count: int
    task_identity: str
    session_identity: str
    strong_session_hash: str
    task_authority_digest: str
    captured_input_item_count: int
    captured_input_fingerprint: str
    non_input_contract_fingerprint: str
    retained_request_direct_call_ledger_digest: str
    captured_projected_payload_fingerprint: str
    captured_actual_wire_fingerprint: str
    captured_request_binding_provenance: str

    def canonical_payload(self) -> dict[str, str | int]:
        return {
            "full_checkpoint_tool_ledger_digest": self.full_checkpoint_tool_ledger_digest,
            "remote_session_jsonl_last_offset": self.remote_session_jsonl_last_offset,
            "remote_session_jsonl_sha256": self.remote_session_jsonl_sha256,
            "remote_session_jsonl_size_bytes": self.remote_session_jsonl_size_bytes,
            "schema": self.schema,
            "session_identity": self.session_identity,
            "strong_session_hash": self.strong_session_hash,
            "task_identity": self.task_identity,
            "task_authority_digest": self.task_authority_digest,
            "captured_input_item_count": self.captured_input_item_count,
            "captured_input_fingerprint": self.captured_input_fingerprint,
            "non_input_contract_fingerprint": self.non_input_contract_fingerprint,
            "retained_request_direct_call_ledger_digest": self.retained_request_direct_call_ledger_digest,
            "captured_projected_payload_fingerprint": self.captured_projected_payload_fingerprint,
            "captured_actual_wire_fingerprint": self.captured_actual_wire_fingerprint,
            "captured_request_binding_provenance": self.captured_request_binding_provenance,
            "unresolved_count": self.unresolved_count,
        }

    def sha256(self) -> str:
        return canonical_json_sha256(self.canonical_payload())


class RowlessRecoveryRepository:
    """Own the non-cascading semantic-rebase authority and its CAS fences."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def capture(
        self,
        *,
        api_key_scope: str,
        session_key_kind: str,
        strong_session_hash: str,
        stale_anchor_hash: str,
        selected_account_intent: str,
        task_identity: str,
        session_identity: str,
        task_authority_digest: str,
        facts: RowlessRecoveryCaptureFacts,
        origin_marker_session_id: str | None = None,
    ) -> RowlessRecoveryAuthoritySnapshot:
        if (
            not api_key_scope
            or not session_key_kind
            or not selected_account_intent
            or not task_identity
            or not session_identity
            or not _is_sha256(task_authority_digest)
        ):
            raise ValueError("rowless recovery identity must be complete")
        generation_nonce = secrets.token_hex(32)
        # A rowless reject proves only which account rejected the stale
        # anchor.  It is not proof that this account owned the now-purged
        # historical anchor.  Only an account-neutral self-contained request
        # can therefore enter this operator rebase flow.
        if not facts.self_contained or not facts.account_neutral:
            raise RowlessRecoveryStateError("rowless_request_not_account_neutral")
        async with sqlite_writer_section():
            if self._session.get_bind().dialect.name == "postgresql":
                await self._session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"http-bridge-rowless-capture:{api_key_scope}"},
                )
            existing = await self._find_for_update(
                api_key_scope=api_key_scope,
                strong_session_hash=strong_session_hash,
                stale_anchor_hash=stale_anchor_hash,
            )
            if existing is not None:
                self._require_same_capture(
                    existing,
                    selected_account_intent,
                    task_identity,
                    session_identity,
                    task_authority_digest,
                    facts,
                    origin_marker_session_id,
                )
                snapshot = _snapshot(existing)
                await self._session.rollback()
                return snapshot
            existing = await self._find_exact_request_contract_for_update(
                api_key_scope=api_key_scope,
                strong_session_hash=strong_session_hash,
                facts=facts,
            )
            if existing is not None:
                self._require_same_capture(
                    existing,
                    selected_account_intent,
                    task_identity,
                    session_identity,
                    task_authority_digest,
                    facts,
                    origin_marker_session_id,
                )
                snapshot = _snapshot(existing)
                await self._session.rollback()
                return snapshot
            captured_count = await self._session.scalar(
                select(func.count())
                .select_from(HttpBridgeRowlessRecoveryAuthority)
                .where(
                    HttpBridgeRowlessRecoveryAuthority.api_key_scope == api_key_scope,
                    HttpBridgeRowlessRecoveryAuthority.state == HttpBridgeRowlessRecoveryState.CAPTURED,
                )
            )
            if int(captured_count or 0) >= ROWLESS_RECOVERY_MAX_CAPTURED_PER_API_SCOPE:
                await self._session.rollback()
                raise RowlessRecoveryStateError("rowless_capture_scope_limit_reached")
            if origin_marker_session_id is not None:
                await self._require_live_origin_marker(
                    session_id=origin_marker_session_id,
                    api_key_scope=api_key_scope,
                    selected_account_intent=selected_account_intent,
                    stale_anchor_hash=stale_anchor_hash,
                )
            row = HttpBridgeRowlessRecoveryAuthority(
                api_key_scope=api_key_scope,
                session_key_kind=session_key_kind,
                strong_session_hash=strong_session_hash,
                stale_anchor_hash=stale_anchor_hash,
                generation=1,
                generation_nonce=generation_nonce,
                state=HttpBridgeRowlessRecoveryState.CAPTURED,
                captured_input_item_count=facts.input_item_count,
                captured_input_fingerprint=facts.input_fingerprint,
                non_input_contract_fingerprint=facts.contract_fingerprint,
                settled_direct_call_ledger_digest=facts.direct_call_ledger_digest,
                projected_payload_fingerprint=facts.projected_payload_fingerprint,
                actual_wire_fingerprint=facts.actual_wire_fingerprint,
                origin_marker_session_id=origin_marker_session_id,
                settled_direct_call_unresolved_count=facts.unresolved_count,
                selected_account_intent=selected_account_intent,
                captured_task_identity_hash=canonical_json_sha256(task_identity),
                captured_session_identity_hash=canonical_json_sha256(session_identity),
                captured_task_authority_digest=task_authority_digest,
                request_self_contained=facts.self_contained,
                request_account_neutral=facts.account_neutral,
            )
            self._session.add(row)
            try:
                await self._session.commit()
            except IntegrityError:
                await self._session.rollback()
                existing = await self._find_for_update(
                    api_key_scope=api_key_scope,
                    strong_session_hash=strong_session_hash,
                    stale_anchor_hash=stale_anchor_hash,
                )
                if existing is None:
                    existing = await self._find_exact_request_contract_for_update(
                        api_key_scope=api_key_scope,
                        strong_session_hash=strong_session_hash,
                        facts=facts,
                    )
                if existing is None:
                    raise
                self._require_same_capture(
                    existing,
                    selected_account_intent,
                    task_identity,
                    session_identity,
                    task_authority_digest,
                    facts,
                    origin_marker_session_id,
                )
                snapshot = _snapshot(existing)
                await self._session.rollback()
                return snapshot
            await self._session.refresh(row)
            return _snapshot(row)

    async def get(self, authority_id: str) -> RowlessRecoveryAuthoritySnapshot | None:
        row = await self._session.get(HttpBridgeRowlessRecoveryAuthority, authority_id)
        return _snapshot(row) if row is not None else None

    async def lookup(
        self,
        *,
        api_key_scope: str,
        strong_session_hash: str,
        stale_anchor_hash: str,
    ) -> RowlessRecoveryAuthoritySnapshot | None:
        row = await self._session.scalar(
            select(HttpBridgeRowlessRecoveryAuthority).where(
                HttpBridgeRowlessRecoveryAuthority.api_key_scope == api_key_scope,
                HttpBridgeRowlessRecoveryAuthority.strong_session_hash == strong_session_hash,
                HttpBridgeRowlessRecoveryAuthority.stale_anchor_hash == stale_anchor_hash,
            )
        )
        return _snapshot(row) if row is not None else None

    async def lookup_exact_request_contract(
        self,
        *,
        api_key_scope: str,
        strong_session_hash: str,
        facts: RowlessRecoveryCaptureFacts,
    ) -> RowlessRecoveryAuthoritySnapshot | None:
        """Resolve one task-bound authority without trusting the incoming anchor."""

        rows = list(
            (
                await self._session.scalars(
                    select(HttpBridgeRowlessRecoveryAuthority)
                    .where(
                        HttpBridgeRowlessRecoveryAuthority.api_key_scope == api_key_scope,
                        HttpBridgeRowlessRecoveryAuthority.strong_session_hash == strong_session_hash,
                        HttpBridgeRowlessRecoveryAuthority.captured_input_item_count == facts.input_item_count,
                        HttpBridgeRowlessRecoveryAuthority.captured_input_fingerprint == facts.input_fingerprint,
                        HttpBridgeRowlessRecoveryAuthority.non_input_contract_fingerprint == facts.contract_fingerprint,
                        HttpBridgeRowlessRecoveryAuthority.settled_direct_call_ledger_digest
                        == facts.direct_call_ledger_digest,
                        HttpBridgeRowlessRecoveryAuthority.projected_payload_fingerprint
                        == facts.projected_payload_fingerprint,
                    )
                    .order_by(HttpBridgeRowlessRecoveryAuthority.created_at.asc())
                    .limit(2)
                )
            ).all()
        )
        if len(rows) > 1:
            raise RowlessRecoveryStateError("rowless_request_authority_ambiguous")
        return _snapshot(rows[0]) if rows else None

    async def list_authorities(
        self,
        *,
        states: tuple[HttpBridgeRowlessRecoveryState, ...] = (
            HttpBridgeRowlessRecoveryState.CAPTURED,
            HttpBridgeRowlessRecoveryState.APPROVED,
            HttpBridgeRowlessRecoveryState.UNKNOWN,
        ),
        limit: int = 100,
    ) -> list[RowlessRecoveryAuthoritySnapshot]:
        rows = (
            await self._session.scalars(
                select(HttpBridgeRowlessRecoveryAuthority)
                .where(HttpBridgeRowlessRecoveryAuthority.state.in_(states))
                .order_by(HttpBridgeRowlessRecoveryAuthority.updated_at.desc())
                .limit(max(1, min(limit, 500)))
            )
        ).all()
        return [_snapshot(row) for row in rows]

    async def authority_state_counts(self, *, marker_bound_only: bool = False) -> dict[str, int]:
        """Return content-free counts used to enforce the image rollback floor."""

        statement = select(
            HttpBridgeRowlessRecoveryAuthority.state,
            func.count(HttpBridgeRowlessRecoveryAuthority.id),
        )
        if marker_bound_only:
            statement = statement.where(HttpBridgeRowlessRecoveryAuthority.origin_marker_session_id.is_not(None))
        statement = statement.group_by(HttpBridgeRowlessRecoveryAuthority.state)
        rows = (await self._session.execute(statement)).all()
        counts = {state.value: 0 for state in HttpBridgeRowlessRecoveryState}
        for state, count in rows:
            counts[state.value] = int(count)
        return counts

    async def purge_expired_audit_rows(
        self,
        *,
        captured_cutoff: datetime,
        batch_size: int = 100,
    ) -> dict[str, int]:
        """Bound abandoned captures while retaining all replay tombstones."""

        counts = {"captured": 0}
        policies = (
            (
                "captured",
                HttpBridgeRowlessRecoveryAuthority.state == HttpBridgeRowlessRecoveryState.CAPTURED,
                HttpBridgeRowlessRecoveryAuthority.checkpoint_receipt_sha256.is_(None),
                HttpBridgeRowlessRecoveryAuthority.updated_at < captured_cutoff,
            ),
        )
        for label, *filters in policies:
            while True:
                ids = list(
                    (
                        await self._session.scalars(
                            select(HttpBridgeRowlessRecoveryAuthority.id)
                            .where(*filters)
                            .order_by(HttpBridgeRowlessRecoveryAuthority.updated_at.asc())
                            .limit(max(1, min(batch_size, 500)))
                        )
                    ).all()
                )
                if not ids:
                    break
                async with sqlite_writer_section():
                    deleted = await self._session.execute(
                        delete(HttpBridgeRowlessRecoveryAuthority)
                        .where(HttpBridgeRowlessRecoveryAuthority.id.in_(ids), *filters)
                        .returning(HttpBridgeRowlessRecoveryAuthority.id)
                    )
                    await self._session.commit()
                counts[label] += len(deleted.scalars().all())
        return counts

    async def issue_challenge(
        self,
        *,
        authority_id: str,
        generation: int,
    ) -> RowlessRecoveryChallenge:
        challenge = secrets.token_urlsafe(32)
        challenge_hash = sha256(challenge.encode("utf-8")).hexdigest()
        expires_at = utcnow() + timedelta(seconds=ROWLESS_RECOVERY_CHALLENGE_TTL_SECONDS)
        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            if row is None or row.generation != generation or row.state != HttpBridgeRowlessRecoveryState.CAPTURED:
                await self._session.rollback()
                raise RowlessRecoveryStateError("captured_generation_not_found")
            row.challenge_nonce_hash = challenge_hash
            row.challenge_expires_at = expires_at
            await self._session.commit()
            await self._session.refresh(row)
            return RowlessRecoveryChallenge(authority=_snapshot(row), challenge=challenge, expires_at=expires_at)

    async def approve(
        self,
        *,
        authority_id: str,
        generation: int,
        challenge: str,
        declared_receipt_sha256: str,
        receipt: RowlessCheckpointReceipt,
        acknowledgement: str,
        approved_actor: str,
        request_id: str | None,
    ) -> RowlessRecoveryAuthoritySnapshot:
        if acknowledgement != ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT:
            raise RowlessRecoveryStateError("semantic_rebase_acknowledgement_required")
        receipt_sha256 = receipt.sha256()
        if receipt_sha256 != declared_receipt_sha256:
            raise RowlessRecoveryStateError("checkpoint_receipt_digest_mismatch")
        if (
            receipt.schema != "qk_http_bridge_rowless_checkpoint_receipt_v1"
            or receipt.unresolved_count != 0
            or receipt.remote_session_jsonl_size_bytes <= 0
            or receipt.remote_session_jsonl_last_offset != receipt.remote_session_jsonl_size_bytes
            or receipt.strong_session_hash == ""
            or not _is_sha256(receipt.remote_session_jsonl_sha256)
            or not _is_sha256(receipt.full_checkpoint_tool_ledger_digest)
            or not _is_sha256(receipt.strong_session_hash)
            or not _is_sha256(receipt.task_authority_digest)
            or receipt.captured_input_item_count <= 0
            or not _is_sha256(receipt.captured_input_fingerprint)
            or not _is_sha256(receipt.non_input_contract_fingerprint)
            or not _is_sha256(receipt.retained_request_direct_call_ledger_digest)
            or not _is_sha256(receipt.captured_projected_payload_fingerprint)
            or not _is_sha256(receipt.captured_actual_wire_fingerprint)
            or receipt.captured_request_binding_provenance != "server_challenge"
            or not receipt.task_identity.strip()
            or not receipt.session_identity.strip()
        ):
            raise RowlessRecoveryStateError("checkpoint_receipt_invalid")
        now = utcnow()
        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.CAPTURED
                or row.challenge_nonce_hash is None
                or row.challenge_expires_at is None
                or not secrets.compare_digest(
                    row.challenge_nonce_hash,
                    sha256(challenge.encode("utf-8")).hexdigest(),
                )
                or to_utc_naive(row.challenge_expires_at) < to_utc_naive(now)
            ):
                await self._session.rollback()
                raise RowlessRecoveryStateError("challenge_or_generation_invalid")
            if (
                row.settled_direct_call_unresolved_count != 0
                or not row.request_self_contained
                or not row.request_account_neutral
                or receipt.strong_session_hash != row.strong_session_hash
                or receipt.task_authority_digest != row.captured_task_authority_digest
                or canonical_json_sha256(receipt.task_identity) != row.captured_task_identity_hash
                or canonical_json_sha256(receipt.session_identity) != row.captured_session_identity_hash
                or receipt.captured_input_item_count != row.captured_input_item_count
                or receipt.captured_input_fingerprint != row.captured_input_fingerprint
                or receipt.non_input_contract_fingerprint != row.non_input_contract_fingerprint
                or receipt.retained_request_direct_call_ledger_digest != row.settled_direct_call_ledger_digest
                or receipt.captured_projected_payload_fingerprint != row.projected_payload_fingerprint
                or receipt.captured_actual_wire_fingerprint != row.actual_wire_fingerprint
            ):
                await self._session.rollback()
                raise RowlessRecoveryStateError("checkpoint_receipt_contract_mismatch")
            row.state = HttpBridgeRowlessRecoveryState.APPROVED
            row.checkpoint_receipt_sha256 = receipt_sha256
            row.checkpoint_jsonl_sha256 = receipt.remote_session_jsonl_sha256
            row.checkpoint_jsonl_size_bytes = receipt.remote_session_jsonl_size_bytes
            row.checkpoint_jsonl_last_offset = receipt.remote_session_jsonl_last_offset
            row.checkpoint_task_identity_hash = canonical_json_sha256(receipt.task_identity)
            row.checkpoint_session_identity_hash = canonical_json_sha256(receipt.session_identity)
            row.checkpoint_strong_session_hash = receipt.strong_session_hash
            row.checkpoint_task_authority_digest = receipt.task_authority_digest
            row.checkpoint_tool_ledger_digest = receipt.full_checkpoint_tool_ledger_digest
            row.approved_by_actor = canonical_json_sha256(approved_actor)
            row.approved_at = now
            row.challenge_nonce_hash = None
            row.challenge_expires_at = None
            self._session.add(
                AuditLog(
                    action="http_bridge_rowless_semantic_rebase_approved",
                    details=json.dumps(
                        {
                            "authority_id": row.id,
                            "generation": row.generation,
                            "receipt_sha256": receipt_sha256,
                            "actor_hash": row.approved_by_actor,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    request_id=request_id,
                )
            )
            await self._session.commit()
            await self._session.refresh(row)
            return _snapshot(row)

    async def claim_dispatch(
        self,
        *,
        authority_id: str,
        generation: int,
        replacement_session_id: str,
        request_id: str,
        wire_request_fingerprint: str,
        model: str | None,
        task_authority_digest: str,
    ) -> RowlessRecoveryAuthoritySnapshot:
        """Bind a preflight-claimed authority to its durable replacement."""

        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            replacement = await self._session.scalar(
                select(HttpBridgeSessionRecord)
                .where(HttpBridgeSessionRecord.id == replacement_session_id)
                .with_for_update()
            )
            rejection = next(
                (
                    reason
                    for reason, rejected in (
                        ("authority_missing", row is None),
                        ("generation", row is not None and row.generation != generation),
                        ("state", row is not None and row.state != HttpBridgeRowlessRecoveryState.UNKNOWN),
                        ("receipt", row is not None and row.checkpoint_receipt_sha256 is None),
                        ("request_id", row is not None and row.dispatch_request_id != request_id),
                        (
                            "wire_fingerprint",
                            row is not None and row.wire_request_fingerprint != wire_request_fingerprint,
                        ),
                        ("replacement_already_bound", row is not None and row.replacement_session_id is not None),
                        (
                            "task_authority",
                            row is not None and row.captured_task_authority_digest != task_authority_digest,
                        ),
                        (
                            "origin_marker_session",
                            row is not None
                            and row.origin_marker_session_id is not None
                            and replacement is not None
                            and replacement.id != row.origin_marker_session_id,
                        ),
                        (
                            "origin_marker_generation",
                            row is not None
                            and row.origin_marker_session_id is not None
                            and not self._origin_marker_matches_authority(
                                replacement,
                                row,
                                expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                                    row,
                                    wire_request_fingerprint,
                                ),
                            ),
                        ),
                        ("replacement_missing", replacement is None),
                        (
                            "api_scope",
                            row is not None
                            and replacement is not None
                            and replacement.api_key_scope != row.api_key_scope,
                        ),
                        (
                            "account",
                            row is not None
                            and replacement is not None
                            and replacement.account_id != row.selected_account_intent,
                        ),
                    )
                    if rejected
                ),
                None,
            )
            if rejection is not None:
                await self._session.rollback()
                raise RowlessRecoveryStateError(f"approved_generation_dispatch_fence_rejected:{rejection}")
            if row is None or replacement is None:  # pragma: no cover - guarded above
                raise AssertionError("validated dispatch rows are required")
            existing = await self._session.scalar(
                select(HttpBridgeRecoveryAttemptRecord).where(
                    HttpBridgeRecoveryAttemptRecord.session_id == replacement_session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == wire_request_fingerprint,
                )
            )
            if existing is not None:
                await self._session.rollback()
                raise RowlessRecoveryStateError("dispatch_already_claimed")
            self._session.add(
                HttpBridgeRecoveryAttemptRecord(
                    session_id=replacement_session_id,
                    request_fingerprint=wire_request_fingerprint,
                    request_id=request_id,
                    account_id=row.selected_account_intent,
                    model=model,
                    replay_safe=False,
                    state=HttpBridgeRecoveryAttemptState.UNKNOWN,
                )
            )
            row.replacement_session_id = replacement_session_id
            try:
                await self._session.commit()
            except IntegrityError as exc:
                await self._session.rollback()
                raise RowlessRecoveryStateError("dispatch_already_claimed") from exc
            await self._session.refresh(row)
            return _snapshot(row)

    async def claim_dispatch_preflight(
        self,
        *,
        authority_id: str,
        generation: int,
        request_id: str,
        wire_request_fingerprint: str,
        task_authority_digest: str,
    ) -> RowlessRecoveryAuthoritySnapshot:
        """CAS before account selection so concurrent losers never connect."""

        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            origin_marker = None
            if row is not None and row.origin_marker_session_id is not None:
                origin_marker = await self._session.scalar(
                    select(HttpBridgeSessionRecord)
                    .where(HttpBridgeSessionRecord.id == row.origin_marker_session_id)
                    .with_for_update()
                )
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.APPROVED
                or row.checkpoint_receipt_sha256 is None
                or row.captured_task_authority_digest != task_authority_digest
                or (
                    row.origin_marker_session_id is not None
                    and not self._origin_marker_matches_authority(origin_marker, row)
                )
            ):
                await self._session.rollback()
                raise RowlessRecoveryStateError("approved_generation_preflight_fence_rejected")
            if origin_marker is not None:
                origin_marker.recovery_required_attempt_fingerprint = _origin_marker_attempt_fingerprint(
                    row,
                    wire_request_fingerprint,
                )
            row.state = HttpBridgeRowlessRecoveryState.UNKNOWN
            row.dispatch_request_id = request_id
            row.wire_request_fingerprint = wire_request_fingerprint
            row.dispatch_send_started_at = None
            await self._session.commit()
            await self._session.refresh(row)
            return _snapshot(row)

    async def rollback_preflight_setup_failure(
        self,
        *,
        authority_id: str,
        generation: int,
        request_id: str,
        wire_request_fingerprint: str,
    ) -> bool:
        """Restore a claim only when setup failed before a replacement existed."""

        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            origin_marker = await self._load_origin_marker_for_update(row)
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.UNKNOWN
                or row.dispatch_request_id != request_id
                or row.wire_request_fingerprint != wire_request_fingerprint
                or row.replacement_session_id is not None
                or row.dispatch_send_started_at is not None
                or (
                    row.origin_marker_session_id is not None
                    and not self._origin_marker_matches_authority(
                        origin_marker,
                        row,
                        expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                            row,
                            wire_request_fingerprint,
                        ),
                    )
                )
            ):
                await self._session.rollback()
                return False
            journal_exists = await self._session.scalar(
                select(HttpBridgeRecoveryAttemptRecord.id).where(
                    HttpBridgeRecoveryAttemptRecord.request_id == request_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == wire_request_fingerprint,
                )
            )
            if journal_exists is not None:
                await self._session.rollback()
                return False
            row.state = HttpBridgeRowlessRecoveryState.APPROVED
            row.dispatch_request_id = None
            row.wire_request_fingerprint = None
            if origin_marker is not None:
                origin_marker.recovery_required_attempt_fingerprint = None
            await self._session.commit()
            return True

    async def mark_dispatch_send_started(
        self,
        *,
        authority_id: str,
        generation: int,
        request_id: str,
        wire_request_fingerprint: str,
    ) -> bool:
        """Durably close the only proven-unsent rollback window before send."""

        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            origin_marker = await self._load_origin_marker_for_update(row)
            journal = None
            if row is not None and row.replacement_session_id is not None:
                journal = await self._session.scalar(
                    select(HttpBridgeRecoveryAttemptRecord)
                    .where(
                        HttpBridgeRecoveryAttemptRecord.session_id == row.replacement_session_id,
                        HttpBridgeRecoveryAttemptRecord.request_id == request_id,
                        HttpBridgeRecoveryAttemptRecord.request_fingerprint == wire_request_fingerprint,
                    )
                    .with_for_update()
                )
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.UNKNOWN
                or row.dispatch_request_id != request_id
                or row.wire_request_fingerprint != wire_request_fingerprint
                or row.dispatch_send_started_at is not None
                or journal is None
                or journal.state != HttpBridgeRecoveryAttemptState.UNKNOWN
                or journal.response_id is not None
                or (
                    row.origin_marker_session_id is not None
                    and not self._origin_marker_matches_authority(
                        origin_marker,
                        row,
                        expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                            row,
                            wire_request_fingerprint,
                        ),
                    )
                )
            ):
                await self._session.rollback()
                return False
            row.dispatch_send_started_at = utcnow()
            await self._session.commit()
            return True

    async def rollback_proven_unsent(
        self,
        *,
        authority_id: str,
        generation: int,
        request_id: str,
        wire_request_fingerprint: str,
    ) -> bool:
        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            origin_marker = await self._load_origin_marker_for_update(row)
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.UNKNOWN
                or row.dispatch_request_id != request_id
                or row.wire_request_fingerprint != wire_request_fingerprint
                or row.dispatch_send_started_at is not None
                or (
                    row.origin_marker_session_id is not None
                    and not self._origin_marker_matches_authority(
                        origin_marker,
                        row,
                        expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                            row,
                            wire_request_fingerprint,
                        ),
                    )
                )
            ):
                await self._session.rollback()
                return False
            if row.replacement_session_id is None:
                await self._session.rollback()
                return False
            deleted = await self._session.execute(
                delete(HttpBridgeRecoveryAttemptRecord)
                .where(
                    HttpBridgeRecoveryAttemptRecord.session_id == row.replacement_session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == wire_request_fingerprint,
                    HttpBridgeRecoveryAttemptRecord.request_id == request_id,
                    HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.UNKNOWN,
                    HttpBridgeRecoveryAttemptRecord.response_id.is_(None),
                )
                .returning(HttpBridgeRecoveryAttemptRecord.id)
            )
            if deleted.scalar_one_or_none() is None:
                await self._session.rollback()
                return False
            row.state = HttpBridgeRowlessRecoveryState.APPROVED
            row.replacement_session_id = None
            row.dispatch_request_id = None
            row.wire_request_fingerprint = None
            if origin_marker is not None:
                origin_marker.recovery_required_attempt_fingerprint = None
            await self._session.commit()
            return True

    async def rollback_physically_unsent_after_send_marker(
        self,
        *,
        authority_id: str,
        generation: int,
        request_id: str,
        wire_request_fingerprint: str,
        transport_proof_code: str,
    ) -> bool:
        """Restore APPROVED after every attempted socket proved no bytes sent."""

        if transport_proof_code != UPSTREAM_WEBSOCKET_CLOSED_BEFORE_SEND_CODE:
            return False
        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            origin_marker = await self._load_origin_marker_for_update(row)
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.UNKNOWN
                or row.dispatch_request_id != request_id
                or row.wire_request_fingerprint != wire_request_fingerprint
                or row.dispatch_send_started_at is None
                or row.replacement_session_id is None
                or (
                    row.origin_marker_session_id is not None
                    and not self._origin_marker_matches_authority(
                        origin_marker,
                        row,
                        expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                            row,
                            wire_request_fingerprint,
                        ),
                    )
                )
            ):
                await self._session.rollback()
                return False
            deleted = await self._session.execute(
                delete(HttpBridgeRecoveryAttemptRecord)
                .where(
                    HttpBridgeRecoveryAttemptRecord.session_id == row.replacement_session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == wire_request_fingerprint,
                    HttpBridgeRecoveryAttemptRecord.request_id == request_id,
                    HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.UNKNOWN,
                    HttpBridgeRecoveryAttemptRecord.response_id.is_(None),
                )
                .returning(HttpBridgeRecoveryAttemptRecord.id)
            )
            if deleted.scalar_one_or_none() is None:
                await self._session.rollback()
                return False
            row.state = HttpBridgeRowlessRecoveryState.APPROVED
            row.replacement_session_id = None
            row.dispatch_request_id = None
            row.dispatch_send_started_at = None
            row.wire_request_fingerprint = None
            if origin_marker is not None:
                origin_marker.recovery_required_attempt_fingerprint = None
            await self._session.commit()
            return True

    async def rollback_before_send_primitive(
        self,
        *,
        authority_id: str,
        generation: int,
        request_id: str,
        wire_request_fingerprint: str,
    ) -> bool:
        """Restore an exact UNKNOWN journal before its send helper is invoked."""

        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            origin_marker = await self._load_origin_marker_for_update(row)
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.UNKNOWN
                or row.dispatch_request_id != request_id
                or row.wire_request_fingerprint != wire_request_fingerprint
                or row.replacement_session_id is None
                or (
                    row.origin_marker_session_id is not None
                    and not self._origin_marker_matches_authority(
                        origin_marker,
                        row,
                        expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                            row,
                            wire_request_fingerprint,
                        ),
                    )
                )
            ):
                await self._session.rollback()
                return False
            deleted = await self._session.execute(
                delete(HttpBridgeRecoveryAttemptRecord)
                .where(
                    HttpBridgeRecoveryAttemptRecord.session_id == row.replacement_session_id,
                    HttpBridgeRecoveryAttemptRecord.request_fingerprint == wire_request_fingerprint,
                    HttpBridgeRecoveryAttemptRecord.request_id == request_id,
                    HttpBridgeRecoveryAttemptRecord.state == HttpBridgeRecoveryAttemptState.UNKNOWN,
                    HttpBridgeRecoveryAttemptRecord.response_id.is_(None),
                )
                .returning(HttpBridgeRecoveryAttemptRecord.id)
            )
            if deleted.scalar_one_or_none() is None:
                await self._session.rollback()
                return False
            row.state = HttpBridgeRowlessRecoveryState.APPROVED
            row.replacement_session_id = None
            row.dispatch_request_id = None
            row.dispatch_send_started_at = None
            row.wire_request_fingerprint = None
            if origin_marker is not None:
                origin_marker.recovery_required_attempt_fingerprint = None
            await self._session.commit()
            return True

    async def settle_completed(
        self,
        *,
        authority_id: str,
        generation: int,
        replacement_session_id: str,
        owner_instance_id: str,
        owner_epoch: int,
        request_id: str,
        response_id: str,
        input_item_count: int,
        input_full_fingerprint: str,
        pending_tool_calls: dict[str, str],
    ) -> bool:
        """Atomically publish the new checkpoint and consume one authority."""

        async with sqlite_writer_section():
            row = await self._find_id_for_update(authority_id)
            replacement = await self._session.scalar(
                select(HttpBridgeSessionRecord)
                .where(HttpBridgeSessionRecord.id == replacement_session_id)
                .with_for_update()
            )
            journal = await self._session.scalar(
                select(HttpBridgeRecoveryAttemptRecord)
                .where(
                    HttpBridgeRecoveryAttemptRecord.session_id == replacement_session_id,
                    HttpBridgeRecoveryAttemptRecord.request_id == request_id,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.generation != generation
                or row.state != HttpBridgeRowlessRecoveryState.UNKNOWN
                or row.replacement_session_id != replacement_session_id
                or row.dispatch_request_id != request_id
                or row.dispatch_send_started_at is None
                or replacement is None
                or replacement.api_key_scope != row.api_key_scope
                or replacement.owner_instance_id != owner_instance_id
                or replacement.owner_epoch != owner_epoch
                or replacement.account_id != row.selected_account_intent
                or journal is None
                or journal.state != HttpBridgeRecoveryAttemptState.UNKNOWN
                or journal.request_fingerprint != row.wire_request_fingerprint
                or journal.response_id is not None
                or input_item_count != row.captured_input_item_count
                or input_full_fingerprint != row.captured_input_fingerprint
                or (
                    row.origin_marker_session_id is not None
                    and (
                        replacement.id != row.origin_marker_session_id
                        or not self._origin_marker_matches_authority(
                            replacement,
                            row,
                            expected_attempt_fingerprint=_origin_marker_attempt_fingerprint(
                                row,
                                row.wire_request_fingerprint,
                            ),
                        )
                    )
                )
            ):
                await self._session.rollback()
                return False
            replacement.latest_response_id = response_id
            replacement.latest_input_item_count = input_item_count
            replacement.latest_input_full_fingerprint = input_full_fingerprint
            replacement.latest_pending_tool_calls_json = _encode_pending_tool_calls(
                response_id,
                pending_tool_calls,
            )
            replacement.recovery_required_anchor_hash = None
            replacement.recovery_required_account_id = None
            replacement.recovery_required_attempt_fingerprint = None
            replacement.recovery_required_at = None
            registered = await DurableBridgeRepository(self._session)._execute_alias_upsert(
                session_id=replacement_session_id,
                alias_kind="previous_response_id",
                alias_value=response_id,
                api_key_scope=row.api_key_scope,
                target_account_neutral_replay=False,
            )
            if not registered:
                await self._session.rollback()
                return False
            journal.state = HttpBridgeRecoveryAttemptState.REPLAYED
            journal.response_id = response_id
            row.state = HttpBridgeRowlessRecoveryState.CONSUMED
            row.consumed_response_id_hash = durable_bridge_hash(response_id)
            row.consumed_at = utcnow()
            self._session.add(
                AuditLog(
                    action="http_bridge_rowless_semantic_rebase_consumed",
                    details=json.dumps(
                        {
                            "authority_id": row.id,
                            "generation": row.generation,
                            "response_id_hash": row.consumed_response_id_hash,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    request_id=request_id,
                )
            )
            await self._session.commit()
            return True

    async def _find_for_update(
        self,
        *,
        api_key_scope: str,
        strong_session_hash: str,
        stale_anchor_hash: str,
    ) -> HttpBridgeRowlessRecoveryAuthority | None:
        return await self._session.scalar(
            select(HttpBridgeRowlessRecoveryAuthority)
            .where(
                HttpBridgeRowlessRecoveryAuthority.api_key_scope == api_key_scope,
                HttpBridgeRowlessRecoveryAuthority.strong_session_hash == strong_session_hash,
                HttpBridgeRowlessRecoveryAuthority.stale_anchor_hash == stale_anchor_hash,
            )
            .with_for_update()
        )

    async def _find_id_for_update(self, authority_id: str) -> HttpBridgeRowlessRecoveryAuthority | None:
        return await self._session.scalar(
            select(HttpBridgeRowlessRecoveryAuthority)
            .where(HttpBridgeRowlessRecoveryAuthority.id == authority_id)
            .with_for_update()
        )

    async def _require_live_origin_marker(
        self,
        *,
        session_id: str,
        api_key_scope: str,
        selected_account_intent: str,
        stale_anchor_hash: str,
    ) -> None:
        marker = await self._session.scalar(
            select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.id == session_id).with_for_update()
        )
        if (
            marker is None
            or marker.api_key_scope != api_key_scope
            or marker.account_id != selected_account_intent
            or marker.recovery_required_account_id != selected_account_intent
            or marker.recovery_required_anchor_hash != stale_anchor_hash
            or marker.latest_response_id is None
            or durable_bridge_hash(marker.latest_response_id) != stale_anchor_hash
            or marker.recovery_required_attempt_fingerprint is not None
        ):
            raise RowlessRecoveryStateError("durable_marker_capture_fence_rejected")

    @staticmethod
    def _origin_marker_matches_authority(
        marker: HttpBridgeSessionRecord | None,
        authority: HttpBridgeRowlessRecoveryAuthority,
        *,
        expected_attempt_fingerprint: str | None = None,
    ) -> bool:
        return bool(
            marker is not None
            and marker.id == authority.origin_marker_session_id
            and marker.api_key_scope == authority.api_key_scope
            and marker.account_id == authority.selected_account_intent
            and marker.recovery_required_account_id == authority.selected_account_intent
            and marker.recovery_required_anchor_hash == authority.stale_anchor_hash
            and marker.latest_response_id is not None
            and durable_bridge_hash(marker.latest_response_id) == authority.stale_anchor_hash
            and marker.recovery_required_attempt_fingerprint == expected_attempt_fingerprint
        )

    async def _load_origin_marker_for_update(
        self,
        authority: HttpBridgeRowlessRecoveryAuthority | None,
    ) -> HttpBridgeSessionRecord | None:
        if authority is None or authority.origin_marker_session_id is None:
            return None
        return await self._session.scalar(
            select(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == authority.origin_marker_session_id)
            .with_for_update()
        )

    async def _find_exact_request_contract_for_update(
        self,
        *,
        api_key_scope: str,
        strong_session_hash: str,
        facts: RowlessRecoveryCaptureFacts,
    ) -> HttpBridgeRowlessRecoveryAuthority | None:
        return await self._session.scalar(
            select(HttpBridgeRowlessRecoveryAuthority)
            .where(
                HttpBridgeRowlessRecoveryAuthority.api_key_scope == api_key_scope,
                HttpBridgeRowlessRecoveryAuthority.strong_session_hash == strong_session_hash,
                HttpBridgeRowlessRecoveryAuthority.captured_input_fingerprint == facts.input_fingerprint,
                HttpBridgeRowlessRecoveryAuthority.non_input_contract_fingerprint == facts.contract_fingerprint,
                HttpBridgeRowlessRecoveryAuthority.settled_direct_call_ledger_digest == facts.direct_call_ledger_digest,
                HttpBridgeRowlessRecoveryAuthority.projected_payload_fingerprint == facts.projected_payload_fingerprint,
            )
            .with_for_update()
        )

    @staticmethod
    def _require_same_capture(
        row: HttpBridgeRowlessRecoveryAuthority,
        selected_account_intent: str,
        task_identity: str,
        session_identity: str,
        task_authority_digest: str,
        facts: RowlessRecoveryCaptureFacts,
        origin_marker_session_id: str | None,
    ) -> None:
        if (
            row.selected_account_intent != selected_account_intent
            or row.origin_marker_session_id != origin_marker_session_id
            or row.captured_task_identity_hash != canonical_json_sha256(task_identity)
            or row.captured_session_identity_hash != canonical_json_sha256(session_identity)
            or row.captured_task_authority_digest != task_authority_digest
            or row.captured_input_item_count != facts.input_item_count
            or row.captured_input_fingerprint != facts.input_fingerprint
            or row.non_input_contract_fingerprint != facts.contract_fingerprint
            or row.settled_direct_call_ledger_digest != facts.direct_call_ledger_digest
            or row.projected_payload_fingerprint != facts.projected_payload_fingerprint
            or row.actual_wire_fingerprint != facts.actual_wire_fingerprint
            or row.settled_direct_call_unresolved_count != facts.unresolved_count
            or row.request_self_contained != facts.self_contained
            or row.request_account_neutral != facts.account_neutral
        ):
            raise RowlessRecoveryConflictError("rowless recovery contract changed")


def _snapshot(row: HttpBridgeRowlessRecoveryAuthority) -> RowlessRecoveryAuthoritySnapshot:
    return RowlessRecoveryAuthoritySnapshot(
        id=row.id,
        api_key_scope=row.api_key_scope,
        session_key_kind=row.session_key_kind,
        strong_session_hash=row.strong_session_hash,
        stale_anchor_hash=row.stale_anchor_hash,
        generation=row.generation,
        generation_nonce=row.generation_nonce,
        state=row.state,
        captured_input_item_count=row.captured_input_item_count,
        captured_input_fingerprint=row.captured_input_fingerprint,
        non_input_contract_fingerprint=row.non_input_contract_fingerprint,
        settled_direct_call_ledger_digest=row.settled_direct_call_ledger_digest,
        projected_payload_fingerprint=row.projected_payload_fingerprint,
        actual_wire_fingerprint=row.actual_wire_fingerprint,
        origin_marker_session_id=row.origin_marker_session_id,
        settled_direct_call_unresolved_count=row.settled_direct_call_unresolved_count,
        selected_account_intent=row.selected_account_intent,
        captured_task_identity_hash=row.captured_task_identity_hash,
        captured_session_identity_hash=row.captured_session_identity_hash,
        captured_task_authority_digest=row.captured_task_authority_digest,
        request_self_contained=row.request_self_contained,
        request_account_neutral=row.request_account_neutral,
        checkpoint_receipt_sha256=row.checkpoint_receipt_sha256,
        replacement_session_id=row.replacement_session_id,
        dispatch_request_id=row.dispatch_request_id,
        dispatch_send_started_at=row.dispatch_send_started_at,
        wire_request_fingerprint=row.wire_request_fingerprint,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _origin_marker_attempt_fingerprint(
    authority: HttpBridgeRowlessRecoveryAuthority,
    wire_request_fingerprint: str | None,
) -> str:
    if wire_request_fingerprint is None:
        raise RowlessRecoveryStateError("rowless_marker_wire_fingerprint_missing")
    return canonical_json_sha256(
        {
            "domain": "qk_http_bridge_rowless_marker_attempt_v1",
            "authority_id": authority.id,
            "generation": authority.generation,
            "wire_request_fingerprint": wire_request_fingerprint,
        }
    )


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_sha256(value: str) -> bool:
    return _SHA256_RE.fullmatch(value) is not None
