from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

import app.modules.proxy.rowless_recovery_api as rowless_recovery_api
from app.core.auth.dashboard_access import admin_principal, guest_principal
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.clients.proxy_websocket import UPSTREAM_WEBSOCKET_CLOSED_BEFORE_SEND_CODE
from app.core.openai.requests import ResponsesRequest
from app.db.models import (
    AuditLog,
    Base,
    HttpBridgeRecoveryAttemptRecord,
    HttpBridgeRecoveryAttemptState,
    HttpBridgeRowlessRecoveryAuthority,
    HttpBridgeRowlessRecoveryState,
    HttpBridgeSessionRecord,
)
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository, durable_bridge_hash
from app.modules.proxy.rowless_recovery import (
    ROWLESS_AUTHORIZATION_MODE_AUTOMATIC,
    ROWLESS_AUTHORIZATION_MODE_OPERATOR,
    ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
    RowlessRecoveryCaptureFacts,
    approved_rowless_recovery_projection,
    build_rowless_recovery_capture_facts,
    canonical_json_sha256,
    rowless_strong_session_hash,
    rowless_task_authority_digest,
)
from app.modules.proxy.rowless_recovery_repository import (
    ROWLESS_RECOVERY_CAPTURED_RETENTION_SECONDS,
    RowlessCheckpointReceipt,
    RowlessRecoveryAuthoritySnapshot,
    RowlessRecoveryRepository,
    RowlessRecoveryStateError,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "principal",
    (
        admin_principal(auth_mode=DashboardAuthMode.DISABLED),
        admin_principal(auth_mode=DashboardAuthMode.STANDARD, actor="password-admin"),
        admin_principal(auth_mode=DashboardAuthMode.TRUSTED_HEADER, actor=""),
        guest_principal(),
    ),
)
async def test_rowless_admin_dependency_rejects_non_trusted_operator(monkeypatch, principal) -> None:
    async def resolve(_request):
        return principal

    monkeypatch.setattr(rowless_recovery_api, "require_dashboard_admin_access", resolve)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(Exception) as exc_info:
        await rowless_recovery_api.require_authenticated_rebase_admin(request)
    assert getattr(exc_info.value, "status_code", None) == 403


@pytest.fixture
async def async_session_factory() -> AsyncIterator[Callable[[], AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    await engine.dispose()


def _facts(*, input_count: int, account_neutral: bool = True) -> RowlessRecoveryCaptureFacts:
    return RowlessRecoveryCaptureFacts(
        input_item_count=input_count,
        input_fingerprint=canonical_json_sha256([{"shape": input_count}]),
        contract_fingerprint=canonical_json_sha256({"model": "gpt-5.6"}),
        direct_call_ledger_digest=canonical_json_sha256({"retained": input_count}),
        projected_payload_fingerprint=canonical_json_sha256({"projected": input_count}),
        actual_wire_fingerprint=canonical_json_sha256({"wire": input_count}),
        unresolved_count=0,
        projected_input=[{"role": "user", "content": "redacted"}],
        self_contained=True,
        account_neutral=account_neutral,
        retains_prior_output=True,
    )


def _receipt(
    *,
    task_id: str,
    strong_session_hash: str,
    full_ledger_pairs: int,
    unresolved_count: int = 0,
    captured_input_item_count: int = 85,
) -> RowlessCheckpointReceipt:
    facts = _facts(input_count=captured_input_item_count)
    return RowlessCheckpointReceipt(
        schema="qk_http_bridge_rowless_checkpoint_receipt_v1",
        remote_session_jsonl_sha256=canonical_json_sha256({"task": task_id}),
        remote_session_jsonl_size_bytes=1000 + full_ledger_pairs,
        remote_session_jsonl_last_offset=1000 + full_ledger_pairs,
        full_checkpoint_tool_ledger_digest=canonical_json_sha256(
            {"domain": "full_checkpoint_tool_ledger_v1", "pairs": full_ledger_pairs}
        ),
        unresolved_count=unresolved_count,
        task_identity=task_id,
        session_identity=task_id,
        strong_session_hash=strong_session_hash,
        task_authority_digest=rowless_task_authority_digest(
            session_id=task_id,
            prompt_cache_key=task_id,
            thread_id=task_id,
        ),
        captured_input_item_count=captured_input_item_count,
        captured_input_fingerprint=facts.input_fingerprint,
        non_input_contract_fingerprint=facts.contract_fingerprint,
        retained_request_direct_call_ledger_digest=facts.direct_call_ledger_digest,
        captured_projected_payload_fingerprint=facts.projected_payload_fingerprint,
        captured_actual_wire_fingerprint=facts.actual_wire_fingerprint,
        captured_request_binding_provenance="server_challenge",
    )


async def _capture_and_challenge(
    session: AsyncSession,
    *,
    task_id: str,
    input_count: int,
    stale_anchor_hash: str | None = None,
    origin_marker_session_id: str | None = None,
) -> tuple[RowlessRecoveryRepository, RowlessRecoveryAuthoritySnapshot, str]:
    repository = RowlessRecoveryRepository(session)
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    strong_hash = rowless_strong_session_hash("task_authority", task_authority_digest)
    captured = await repository.capture(
        api_key_scope="key-scope",
        session_key_kind="session_header",
        strong_session_hash=strong_hash,
        stale_anchor_hash=stale_anchor_hash or canonical_json_sha256({"anchor": task_id}),
        selected_account_intent="account-a",
        task_identity=task_id,
        session_identity=task_id,
        task_authority_digest=task_authority_digest,
        facts=_facts(input_count=input_count),
        origin_marker_session_id=origin_marker_session_id,
    )
    challenge = await repository.issue_challenge(
        authority_id=captured.id,
        generation=captured.generation,
    )
    return repository, captured, challenge.challenge


@pytest.mark.asyncio
async def test_rowless_capture_rejects_account_scoped_request(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)
        with pytest.raises(RowlessRecoveryStateError, match="rowless_request_not_account_neutral"):
            await repository.capture(
                api_key_scope="key-scope",
                session_key_kind="session_header",
                strong_session_hash=rowless_strong_session_hash("session_header", "task-a"),
                stale_anchor_hash="a" * 64,
                selected_account_intent="account-b-that-rejected",
                task_identity="task-a",
                session_identity="task-a",
                task_authority_digest=rowless_task_authority_digest(
                    session_id="task-a",
                    prompt_cache_key="task-a",
                    thread_id="task-a",
                ),
                facts=_facts(input_count=85, account_neutral=False),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_id", "retained_input_count", "full_ledger_pairs"),
    (
        ("01a02f21-77a1-7cc2-a892-b6abac317deb", 85, 93),
        ("01a02edf-376c-7c13-a7de-08715e492fab", 288, 110),
        ("01a0287a-6709-7cc3-9bfd-b1967f4af3f5", 194, 1186),
    ),
)
async def test_rowless_approval_separates_full_checkpoint_and_retained_request_ledger_domains(
    async_session_factory: Callable[[], AsyncSession],
    task_id: str,
    retained_input_count: int,
    full_ledger_pairs: int,
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id=task_id,
            input_count=retained_input_count,
        )
        receipt = _receipt(
            task_id=task_id,
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=full_ledger_pairs,
            captured_input_item_count=retained_input_count,
        )
        assert receipt.full_checkpoint_tool_ledger_digest != captured.settled_direct_call_ledger_digest

        approved = await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id="admin-request",
        )

        assert approved.state == HttpBridgeRowlessRecoveryState.APPROVED
        assert approved.captured_input_item_count == retained_input_count
        assert approved.authorization_mode == ROWLESS_AUTHORIZATION_MODE_OPERATOR
        assert approved.authorization_proof_sha256 == receipt.sha256()


@pytest.mark.asyncio
async def test_automatic_live_request_claims_new_authority_without_operator_approval(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_id = "task-automatic-live"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    strong_hash = rowless_strong_session_hash("task_authority", task_authority_digest)
    facts = _facts(input_count=122)

    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)
        claimed = await repository.capture_and_claim_automatic_preflight(
            api_key_scope="key-scope",
            session_key_kind="session_header",
            strong_session_hash=strong_hash,
            stale_anchor_hash="a" * 64,
            selected_account_intent="account-a",
            task_identity=task_id,
            session_identity=task_id,
            task_authority_digest=task_authority_digest,
            facts=facts,
            request_id="request-automatic",
            wire_request_fingerprint=facts.actual_wire_fingerprint,
        )

        assert claimed.generation == 1
        assert claimed.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        assert claimed.authorization_mode == ROWLESS_AUTHORIZATION_MODE_AUTOMATIC
        assert claimed.authorization_proof_sha256 is not None
        assert claimed.checkpoint_receipt_sha256 is None
        assert claimed.dispatch_request_id == "request-automatic"
        assert await repository.active_automatic_authority_count() == 1
        assert await repository.rollback_preflight_setup_failure(
            authority_id=claimed.id,
            generation=claimed.generation,
            request_id="request-automatic",
            wire_request_fingerprint=facts.actual_wire_fingerprint,
        )
        restored = await repository.get(claimed.id)
        assert restored is not None
        assert restored.state == HttpBridgeRowlessRecoveryState.APPROVED
        assert restored.authorization_mode == ROWLESS_AUTHORIZATION_MODE_AUTOMATIC
        reclaimed = await repository.claim_dispatch_preflight(
            authority_id=restored.id,
            generation=restored.generation,
            request_id="request-automatic-retry",
            wire_request_fingerprint=facts.actual_wire_fingerprint,
            task_authority_digest=restored.captured_task_authority_digest,
        )
        assert reclaimed.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        assert await repository.active_automatic_authority_count() == 1


@pytest.mark.asyncio
async def test_automatic_live_request_maps_flush_uniqueness_conflict_to_stable_rejection(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task-automatic-flush-conflict"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    facts = _facts(input_count=124)

    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)

        async def raise_uniqueness_conflict(*args, **kwargs) -> None:
            del args, kwargs
            raise IntegrityError("INSERT", {}, RuntimeError("duplicate task contract"))

        monkeypatch.setattr(session, "flush", raise_uniqueness_conflict)
        with pytest.raises(RowlessRecoveryStateError, match="automatic_live_request_claim_conflict"):
            await repository.capture_and_claim_automatic_preflight(
                api_key_scope="key-scope",
                session_key_kind="session_header",
                strong_session_hash=rowless_strong_session_hash("task_authority", task_authority_digest),
                stale_anchor_hash="d" * 64,
                selected_account_intent="account-a",
                task_identity=task_id,
                session_identity=task_id,
                task_authority_digest=task_authority_digest,
                facts=facts,
                request_id="request-flush-conflict",
                wire_request_fingerprint=facts.actual_wire_fingerprint,
            )
        assert await session.scalar(select(HttpBridgeRowlessRecoveryAuthority.id)) is None


@pytest.mark.asyncio
async def test_automatic_live_request_cancellation_after_commit_restores_unsent_claim(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "task-automatic-cancel-after-commit"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    strong_hash = rowless_strong_session_hash("task_authority", task_authority_digest)
    facts = _facts(input_count=123)

    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)
        real_commit = session.commit
        commit_finished = asyncio.Event()
        release_commit = asyncio.Event()

        async def commit_then_pause() -> None:
            await real_commit()
            commit_finished.set()
            await release_commit.wait()

        monkeypatch.setattr(session, "commit", commit_then_pause)
        claim_task = asyncio.create_task(
            repository.capture_and_claim_automatic_preflight(
                api_key_scope="key-scope",
                session_key_kind="session_header",
                strong_session_hash=strong_hash,
                stale_anchor_hash="c" * 64,
                selected_account_intent="account-a",
                task_identity=task_id,
                session_identity=task_id,
                task_authority_digest=task_authority_digest,
                facts=facts,
                request_id="request-cancel-after-commit",
                wire_request_fingerprint=facts.actual_wire_fingerprint,
            )
        )
        await asyncio.wait_for(commit_finished.wait(), timeout=2.0)
        claim_task.cancel()
        release_commit.set()

        with pytest.raises(asyncio.CancelledError):
            await claim_task

        restored = await session.scalar(select(HttpBridgeRowlessRecoveryAuthority))
        assert restored is not None
        assert restored.state == HttpBridgeRowlessRecoveryState.APPROVED
        assert restored.dispatch_request_id is None
        assert restored.wire_request_fingerprint is None
        assert restored.dispatch_send_started_at is None


@pytest.mark.asyncio
async def test_automatic_live_request_supersedes_only_an_unsent_operator_generation(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_id = "task-automatic-supersede"
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id=task_id,
            input_count=122,
            stale_anchor_hash="b" * 64,
        )
        receipt = _receipt(
            task_id=task_id,
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=120,
            captured_input_item_count=122,
        )
        approved = await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id="approval-request",
        )
        changed_facts = _facts(input_count=124)
        claimed = await repository.capture_and_claim_automatic_preflight(
            api_key_scope=approved.api_key_scope,
            session_key_kind=approved.session_key_kind,
            strong_session_hash=approved.strong_session_hash,
            stale_anchor_hash=approved.stale_anchor_hash,
            selected_account_intent=approved.selected_account_intent,
            task_identity=task_id,
            session_identity=task_id,
            task_authority_digest=approved.captured_task_authority_digest,
            facts=changed_facts,
            request_id="automatic-request",
            wire_request_fingerprint=changed_facts.actual_wire_fingerprint,
            expected_authority_id=approved.id,
            expected_generation=approved.generation,
        )

        assert claimed.id == approved.id
        assert claimed.generation == approved.generation + 1
        assert claimed.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        assert claimed.captured_input_item_count == 124
        assert claimed.authorization_mode == ROWLESS_AUTHORIZATION_MODE_AUTOMATIC
        assert claimed.checkpoint_receipt_sha256 is None
        assert claimed.authorization_proof_sha256 is not None
        audit_actions = list(await session.scalars(select(AuditLog.action)))
        assert "http_bridge_rowless_automatic_generation_superseded" in audit_actions
        assert "http_bridge_rowless_automatic_live_request_claimed" in audit_actions

        with pytest.raises(RowlessRecoveryStateError, match="automatic_unsent_generation_fence_rejected"):
            await repository.capture_and_claim_automatic_preflight(
                api_key_scope=claimed.api_key_scope,
                session_key_kind=claimed.session_key_kind,
                strong_session_hash=claimed.strong_session_hash,
                stale_anchor_hash=claimed.stale_anchor_hash,
                selected_account_intent=claimed.selected_account_intent,
                task_identity=task_id,
                session_identity=task_id,
                task_authority_digest=claimed.captured_task_authority_digest,
                facts=_facts(input_count=126),
                request_id="unsafe-second-request",
                wire_request_fingerprint=_facts(input_count=126).actual_wire_fingerprint,
                expected_authority_id=claimed.id,
                expected_generation=claimed.generation,
            )


@pytest.mark.asyncio
async def test_automatic_live_request_ignores_historical_replayed_marker_journal(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_id = "task-automatic-replayed-journal"
    rejected_anchor = "resp-automatic-replayed-journal"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    strong_hash = rowless_strong_session_hash("task_authority", task_authority_digest)

    async with async_session_factory() as session:
        marker = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value=task_id,
            session_key_hash=canonical_json_sha256(task_id),
            api_key_scope="key-scope",
            account_id="account-a",
            owner_instance_id="instance-a",
            owner_epoch=3,
            latest_response_id=rejected_anchor,
            recovery_required_anchor_hash=durable_bridge_hash(rejected_anchor),
            recovery_required_account_id="account-a",
            recovery_required_at=datetime.now(timezone.utc),
        )
        session.add(marker)
        await session.commit()

        repository = RowlessRecoveryRepository(session)
        captured = await repository.capture(
            api_key_scope="key-scope",
            session_key_kind="session_header",
            strong_session_hash=strong_hash,
            stale_anchor_hash=durable_bridge_hash(rejected_anchor),
            selected_account_intent="account-a",
            task_identity=task_id,
            session_identity=task_id,
            task_authority_digest=task_authority_digest,
            facts=_facts(input_count=122),
            origin_marker_session_id=marker.id,
        )
        challenge = await repository.issue_challenge(
            authority_id=captured.id,
            generation=captured.generation,
        )
        receipt = _receipt(
            task_id=task_id,
            strong_session_hash=strong_hash,
            full_ledger_pairs=120,
            captured_input_item_count=122,
        )
        approved = await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge.challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id="approval-request",
        )
        session.add(
            HttpBridgeRecoveryAttemptRecord(
                session_id=marker.id,
                request_fingerprint="e" * 64,
                request_id="historical-request",
                account_id="account-a",
                model="gpt-5.6",
                replay_safe=False,
                state=HttpBridgeRecoveryAttemptState.REPLAYED,
                response_id="resp-historical-complete",
            )
        )
        await session.commit()

        changed_facts = _facts(input_count=124)
        claimed = await repository.capture_and_claim_automatic_preflight(
            api_key_scope=approved.api_key_scope,
            session_key_kind=approved.session_key_kind,
            strong_session_hash=approved.strong_session_hash,
            stale_anchor_hash=approved.stale_anchor_hash,
            selected_account_intent=approved.selected_account_intent,
            task_identity=task_id,
            session_identity=task_id,
            task_authority_digest=approved.captured_task_authority_digest,
            facts=changed_facts,
            request_id="automatic-request",
            wire_request_fingerprint=changed_facts.actual_wire_fingerprint,
            origin_marker_session_id=marker.id,
            expected_authority_id=approved.id,
            expected_generation=approved.generation,
        )

        assert claimed.generation == approved.generation + 1
        assert claimed.state == HttpBridgeRowlessRecoveryState.UNKNOWN


@pytest.mark.asyncio
async def test_automatic_live_request_concurrent_claim_has_one_winner(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_id = "task-automatic-concurrent"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    strong_hash = rowless_strong_session_hash("task_authority", task_authority_digest)
    facts = _facts(input_count=128)

    async def claim(request_id: str) -> RowlessRecoveryAuthoritySnapshot | Exception:
        try:
            async with async_session_factory() as session:
                return await RowlessRecoveryRepository(session).capture_and_claim_automatic_preflight(
                    api_key_scope="key-scope",
                    session_key_kind="session_header",
                    strong_session_hash=strong_hash,
                    stale_anchor_hash="d" * 64,
                    selected_account_intent="account-a",
                    task_identity=task_id,
                    session_identity=task_id,
                    task_authority_digest=task_authority_digest,
                    facts=facts,
                    request_id=request_id,
                    wire_request_fingerprint=facts.actual_wire_fingerprint,
                )
        except Exception as exc:
            return exc

    outcomes = await asyncio.gather(claim("concurrent-a"), claim("concurrent-b"))
    winners = [outcome for outcome in outcomes if isinstance(outcome, RowlessRecoveryAuthoritySnapshot)]
    losers = [outcome for outcome in outcomes if isinstance(outcome, Exception)]

    assert len(winners) == 1
    assert winners[0].state == HttpBridgeRowlessRecoveryState.UNKNOWN
    assert len(losers) == 1
    assert isinstance(losers[0], RowlessRecoveryStateError)
    assert "automatic_unsent_generation_fence_rejected" in str(losers[0])


@pytest.mark.asyncio
async def test_rowless_approval_rejects_cross_task_receipt_and_unresolved_checkpoint(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-a",
            input_count=85,
        )
        cross_task = _receipt(
            task_id="task-b",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        with pytest.raises(RowlessRecoveryStateError, match="checkpoint_receipt_contract_mismatch"):
            await repository.approve(
                authority_id=captured.id,
                generation=captured.generation,
                challenge=challenge,
                declared_receipt_sha256=cross_task.sha256(),
                receipt=cross_task,
                acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
                approved_actor="dashboard-admin",
                request_id=None,
            )

        unresolved = _receipt(
            task_id="task-a",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
            unresolved_count=1,
        )
        with pytest.raises(RowlessRecoveryStateError, match="checkpoint_receipt_invalid"):
            await repository.approve(
                authority_id=captured.id,
                generation=captured.generation,
                challenge=challenge,
                declared_receipt_sha256=unresolved.sha256(),
                receipt=unresolved,
                acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
                approved_actor="dashboard-admin",
                request_id=None,
            )

        exact = _receipt(
            task_id="task-a",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        for drifted in (
            replace(exact, captured_input_item_count=exact.captured_input_item_count + 1),
            replace(exact, captured_input_fingerprint="c" * 64),
            replace(exact, non_input_contract_fingerprint="d" * 64),
            replace(exact, retained_request_direct_call_ledger_digest="e" * 64),
        ):
            with pytest.raises(RowlessRecoveryStateError, match="checkpoint_receipt_contract_mismatch"):
                await repository.approve(
                    authority_id=captured.id,
                    generation=captured.generation,
                    challenge=challenge,
                    declared_receipt_sha256=drifted.sha256(),
                    receipt=drifted,
                    acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
                    approved_actor="dashboard-admin",
                    request_id=None,
                )


@pytest.mark.asyncio
async def test_rowless_dispatch_binds_replacement_session_and_send_started_fences_rollback(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-a",
            input_count=85,
        )
        receipt = _receipt(
            task_id="task-a",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id=None,
        )
        wrong_session = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value="task-b",
            session_key_hash=canonical_json_sha256("task-b"),
            api_key_scope="key-scope",
            account_id="account-a",
        )
        session.add(wrong_session)
        await session.commit()
        with pytest.raises(RowlessRecoveryStateError, match="approved_generation_dispatch_fence_rejected"):
            await repository.claim_dispatch(
                authority_id=captured.id,
                generation=captured.generation,
                replacement_session_id=wrong_session.id,
                request_id="request-wrong",
                wire_request_fingerprint="a" * 64,
                model="gpt-5.6",
                task_authority_digest=captured.captured_task_authority_digest,
            )

        replacement = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value="task-a",
            session_key_hash=canonical_json_sha256("task-a"),
            api_key_scope="key-scope",
            account_id="account-a",
        )
        session.add(replacement)
        await session.commit()
        replacement_id = replacement.id
        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="b" * 64,
            task_authority_digest=captured.captured_task_authority_digest,
        )
        claimed = await repository.claim_dispatch(
            authority_id=captured.id,
            generation=captured.generation,
            replacement_session_id=replacement_id,
            request_id="request-a",
            wire_request_fingerprint="b" * 64,
            model="gpt-5.6",
            task_authority_digest=captured.captured_task_authority_digest,
        )
        assert claimed.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        assert claimed.dispatch_send_started_at is None
        assert await repository.mark_dispatch_send_started(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="b" * 64,
        )
        assert not await repository.rollback_proven_unsent(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="b" * 64,
        )
        stored = await repository.get(captured.id)
        assert stored is not None
        assert stored.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        assert stored.dispatch_send_started_at is not None
        assert stored.dispatch_send_started_at.tzinfo in (None, timezone.utc)
        attempt = await session.scalar(select(HttpBridgeRecoveryAttemptRecord))
        assert attempt is not None
        assert not await repository.rollback_physically_unsent_after_send_marker(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="b" * 64,
            transport_proof_code="stream_incomplete",
        )
        assert await repository.rollback_physically_unsent_after_send_marker(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="b" * 64,
            transport_proof_code=UPSTREAM_WEBSOCKET_CLOSED_BEFORE_SEND_CODE,
        )
        restored = await repository.get(captured.id)
        assert restored is not None
        assert restored.state == HttpBridgeRowlessRecoveryState.APPROVED
        assert restored.dispatch_send_started_at is None
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None


@pytest.mark.asyncio
async def test_marker_backed_admin_and_automatic_recovery_claims_are_mutually_exclusive(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    rejected_anchor = "resp-marker-exclusive"
    task_id = "task-marker-exclusive"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    strong_hash = rowless_strong_session_hash("task_authority", task_authority_digest)
    admin_wire_fingerprint = "a" * 64
    # Use the exact same raw wire fingerprint to cover the prior bug: the
    # marker's legacy idempotency rule treated equal fingerprints as the same
    # winner even when one belonged to the admin semantic-rebase authority.
    automatic_wire_fingerprint = admin_wire_fingerprint

    async with async_session_factory() as session:
        marker = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value=task_id,
            session_key_hash=canonical_json_sha256(task_id),
            api_key_scope="key-scope",
            account_id="account-a",
            owner_instance_id="instance-a",
            owner_epoch=7,
            latest_response_id=rejected_anchor,
            recovery_required_anchor_hash=durable_bridge_hash(rejected_anchor),
            recovery_required_account_id="account-a",
            recovery_required_at=datetime.now(timezone.utc),
        )
        session.add(marker)
        await session.commit()
        marker_id = marker.id

        repository = RowlessRecoveryRepository(session)
        captured = await repository.capture(
            api_key_scope="key-scope",
            session_key_kind="session_header",
            strong_session_hash=strong_hash,
            stale_anchor_hash=durable_bridge_hash(rejected_anchor),
            selected_account_intent="account-a",
            task_identity=task_id,
            session_identity=task_id,
            task_authority_digest=task_authority_digest,
            facts=_facts(input_count=85),
            origin_marker_session_id=marker_id,
        )
        challenge = await repository.issue_challenge(
            authority_id=captured.id,
            generation=captured.generation,
        )
        receipt = _receipt(
            task_id=task_id,
            strong_session_hash=strong_hash,
            full_ledger_pairs=93,
        )
        approved = await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge.challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id="approval-request",
        )
        assert approved.state == HttpBridgeRowlessRecoveryState.APPROVED

        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-dispatch",
            wire_request_fingerprint=admin_wire_fingerprint,
            task_authority_digest=task_authority_digest,
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is not None
        assert marker.recovery_required_attempt_fingerprint != admin_wire_fingerprint
        assert not await DurableBridgeRepository(session).claim_recovery_required_attempt(
            session_id=marker_id,
            instance_id="instance-a",
            owner_epoch=7,
            account_id="account-a",
            rejected_response_id=rejected_anchor,
            attempt_fingerprint=automatic_wire_fingerprint,
            request_id="generic-marker-loser",
        )
        assert await repository.rollback_preflight_setup_failure(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-dispatch",
            wire_request_fingerprint=admin_wire_fingerprint,
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is None

        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-journal",
            wire_request_fingerprint=admin_wire_fingerprint,
            task_authority_digest=task_authority_digest,
        )
        await repository.claim_dispatch(
            authority_id=captured.id,
            generation=captured.generation,
            replacement_session_id=marker_id,
            request_id="admin-journal",
            wire_request_fingerprint=admin_wire_fingerprint,
            model="gpt-5.6",
            task_authority_digest=task_authority_digest,
        )
        assert await repository.rollback_proven_unsent(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-journal",
            wire_request_fingerprint=admin_wire_fingerprint,
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is None
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None

        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-cancelled-before-send",
            wire_request_fingerprint=admin_wire_fingerprint,
            task_authority_digest=task_authority_digest,
        )
        await repository.claim_dispatch(
            authority_id=captured.id,
            generation=captured.generation,
            replacement_session_id=marker_id,
            request_id="admin-cancelled-before-send",
            wire_request_fingerprint=admin_wire_fingerprint,
            model="gpt-5.6",
            task_authority_digest=task_authority_digest,
        )
        assert await repository.mark_dispatch_send_started(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-cancelled-before-send",
            wire_request_fingerprint=admin_wire_fingerprint,
        )
        assert await repository.rollback_before_send_primitive(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-cancelled-before-send",
            wire_request_fingerprint=admin_wire_fingerprint,
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is None
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None

        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-send-marker",
            wire_request_fingerprint=admin_wire_fingerprint,
            task_authority_digest=task_authority_digest,
        )
        await repository.claim_dispatch(
            authority_id=captured.id,
            generation=captured.generation,
            replacement_session_id=marker_id,
            request_id="admin-send-marker",
            wire_request_fingerprint=admin_wire_fingerprint,
            model="gpt-5.6",
            task_authority_digest=task_authority_digest,
        )
        assert await repository.mark_dispatch_send_started(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-send-marker",
            wire_request_fingerprint=admin_wire_fingerprint,
        )
        assert not await repository.rollback_physically_unsent_after_send_marker(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-send-marker",
            wire_request_fingerprint=admin_wire_fingerprint,
            transport_proof_code="stream_incomplete",
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is not None
        ambiguous_authority = await repository.get(captured.id)
        assert ambiguous_authority is not None
        assert ambiguous_authority.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        ambiguous_attempt = await session.scalar(select(HttpBridgeRecoveryAttemptRecord))
        assert ambiguous_attempt is not None
        assert ambiguous_attempt.state == HttpBridgeRecoveryAttemptState.UNKNOWN
        assert await repository.rollback_physically_unsent_after_send_marker(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="admin-send-marker",
            wire_request_fingerprint=admin_wire_fingerprint,
            transport_proof_code=UPSTREAM_WEBSOCKET_CLOSED_BEFORE_SEND_CODE,
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is None
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None

        assert await DurableBridgeRepository(session).claim_recovery_required_attempt(
            session_id=marker_id,
            instance_id="instance-a",
            owner_epoch=7,
            account_id="account-a",
            rejected_response_id=rejected_anchor,
            attempt_fingerprint=automatic_wire_fingerprint,
            request_id="generic-marker-owner",
        )
        assert not await DurableBridgeRepository(session).claim_recovery_required_attempt(
            session_id=marker_id,
            instance_id="instance-a",
            owner_epoch=7,
            account_id="account-a",
            rejected_response_id=rejected_anchor,
            attempt_fingerprint=automatic_wire_fingerprint,
            request_id="generic-marker-sibling",
        )
        with pytest.raises(RowlessRecoveryStateError, match="approved_generation_preflight_fence_rejected"):
            await repository.claim_dispatch_preflight(
                authority_id=captured.id,
                generation=captured.generation,
                request_id="admin-loser",
                wire_request_fingerprint=admin_wire_fingerprint,
                task_authority_digest=task_authority_digest,
            )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint == automatic_wire_fingerprint
        marker_repository = DurableBridgeRepository(session)
        assert not await marker_repository.rollback_recovery_required_attempt_before_dispatch(
            session_id=marker_id,
            api_key_scope="key-scope",
            instance_id="instance-a",
            owner_epoch=8,
            account_id="account-a",
            attempt_fingerprint=automatic_wire_fingerprint,
            request_id="generic-marker-owner",
            journal_request_id="generic-journal-owner",
        )
        assert not await marker_repository.rollback_recovery_required_attempt_before_dispatch(
            session_id=marker_id,
            api_key_scope="key-scope",
            instance_id="instance-a",
            owner_epoch=7,
            account_id="account-a",
            attempt_fingerprint=automatic_wire_fingerprint,
            request_id="generic-marker-sibling",
            journal_request_id="generic-journal-owner",
        )
        session.add(
            HttpBridgeRecoveryAttemptRecord(
                session_id=marker_id,
                request_fingerprint=automatic_wire_fingerprint,
                request_id="generic-journal-owner",
                account_id="account-a",
                model="gpt-5.6-sol",
                replay_safe=True,
                state=HttpBridgeRecoveryAttemptState.UNKNOWN,
            )
        )
        await session.commit()
        assert await marker_repository.rollback_recovery_required_attempt_before_dispatch(
            session_id=marker_id,
            api_key_scope="key-scope",
            instance_id="instance-a",
            owner_epoch=7,
            account_id="account-a",
            attempt_fingerprint=automatic_wire_fingerprint,
            request_id="generic-marker-owner",
            journal_request_id="generic-journal-owner",
        )
        await session.refresh(marker)
        assert marker.recovery_required_attempt_fingerprint is None
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None

        await session.delete(marker)
        await session.commit()
        with pytest.raises(RowlessRecoveryStateError, match="approved_generation_preflight_fence_rejected"):
            await repository.claim_dispatch_preflight(
                authority_id=captured.id,
                generation=captured.generation,
                request_id="admin-missing-origin",
                wire_request_fingerprint=admin_wire_fingerprint,
                task_authority_digest=task_authority_digest,
            )


@pytest.mark.asyncio
async def test_marker_admin_and_automatic_claims_have_one_concurrent_winner(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    rejected_anchor = "resp-marker-concurrent-mixed"
    task_id = "task-marker-concurrent-mixed"
    task_authority_digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )
    wire_fingerprint = "d" * 64
    async with async_session_factory() as session:
        marker = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value=task_id,
            session_key_hash=canonical_json_sha256(task_id),
            api_key_scope="key-scope",
            account_id="account-a",
            owner_instance_id="instance-a",
            owner_epoch=9,
            latest_response_id=rejected_anchor,
            recovery_required_anchor_hash=durable_bridge_hash(rejected_anchor),
            recovery_required_account_id="account-a",
            recovery_required_at=datetime.now(timezone.utc),
        )
        session.add(marker)
        await session.commit()
        marker_id = marker.id
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id=task_id,
            input_count=85,
            stale_anchor_hash=durable_bridge_hash(rejected_anchor),
            origin_marker_session_id=marker_id,
        )
        receipt = _receipt(
            task_id=task_id,
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id="approval-request",
        )

    async def claim_admin() -> bool:
        async with async_session_factory() as session:
            try:
                await RowlessRecoveryRepository(session).claim_dispatch_preflight(
                    authority_id=captured.id,
                    generation=captured.generation,
                    request_id="admin-concurrent",
                    wire_request_fingerprint=wire_fingerprint,
                    task_authority_digest=task_authority_digest,
                )
            except RowlessRecoveryStateError:
                return False
            return True

    async def claim_automatic() -> bool:
        async with async_session_factory() as session:
            return await DurableBridgeRepository(session).claim_recovery_required_attempt(
                session_id=marker_id,
                instance_id="instance-a",
                owner_epoch=9,
                account_id="account-a",
                rejected_response_id=rejected_anchor,
                attempt_fingerprint=wire_fingerprint,
                request_id="generic-concurrent",
            )

    winners = await asyncio.gather(claim_admin(), claim_automatic())
    assert winners.count(True) == 1
    async with async_session_factory() as session:
        marker = await session.get(HttpBridgeSessionRecord, marker_id)
        assert marker is not None
        assert marker.recovery_required_attempt_fingerprint is not None


@pytest.mark.asyncio
async def test_marker_claim_snapshot_does_not_require_post_commit_refresh(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch,
) -> None:
    rejected_anchor = "resp-marker-no-post-commit-refresh"
    task_id = "task-marker-no-post-commit-refresh"
    async with async_session_factory() as session:
        marker = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value=task_id,
            session_key_hash=canonical_json_sha256(task_id),
            api_key_scope="key-scope",
            account_id="account-a",
            owner_instance_id="instance-a",
            owner_epoch=11,
            latest_response_id=rejected_anchor,
            recovery_required_anchor_hash=durable_bridge_hash(rejected_anchor),
            recovery_required_account_id="account-a",
            recovery_required_at=datetime.now(timezone.utc),
        )
        session.add(marker)
        await session.commit()

        async def fail_refresh(*args, **kwargs):
            del args, kwargs
            raise AssertionError("claim must not await after a successful commit")

        monkeypatch.setattr(session, "refresh", fail_refresh)
        claimed = await DurableBridgeRepository(session).claim_recovery_required_attempt_with_journal(
            session_id=marker.id,
            instance_id="instance-a",
            owner_epoch=11,
            account_id="account-a",
            rejected_response_id=rejected_anchor,
            attempt_fingerprint="e" * 64,
            claim_request_id="claim-request",
            journal_request_id="journal-request",
            model="gpt-5.6",
        )

        assert claimed is not None
        assert claimed.request_id == "journal-request"
        assert claimed.state == HttpBridgeRecoveryAttemptState.UNKNOWN


@pytest.mark.asyncio
async def test_rowless_dispatch_can_rollback_only_before_send_started(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-a",
            input_count=85,
        )
        receipt = _receipt(
            task_id="task-a",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id=None,
        )
        replacement = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value="task-a",
            session_key_hash=canonical_json_sha256("task-a"),
            api_key_scope="key-scope",
            account_id="account-a",
        )
        session.add(replacement)
        await session.commit()
        replacement_id = replacement.id
        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="c" * 64,
            task_authority_digest=captured.captured_task_authority_digest,
        )
        await repository.claim_dispatch(
            authority_id=captured.id,
            generation=captured.generation,
            replacement_session_id=replacement_id,
            request_id="request-a",
            wire_request_fingerprint="c" * 64,
            model="gpt-5.6",
            task_authority_digest=captured.captured_task_authority_digest,
        )
        assert await repository.rollback_proven_unsent(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-a",
            wire_request_fingerprint="c" * 64,
        )
        stored = await repository.get(captured.id)
        assert stored is not None
        assert stored.state == HttpBridgeRowlessRecoveryState.APPROVED
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None


@pytest.mark.asyncio
async def test_rowless_approval_requires_exact_explicit_acknowledgement(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-ack",
            input_count=85,
        )
        receipt = _receipt(
            task_id="task-ack",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        for acknowledgement in ("", "OPERATOR_ACKNOWLEDGED_SEMANTIC_REBASE", "operator_acknowledged"):
            with pytest.raises(RowlessRecoveryStateError, match="semantic_rebase_acknowledgement_required"):
                await repository.approve(
                    authority_id=captured.id,
                    generation=captured.generation,
                    challenge=challenge,
                    declared_receipt_sha256=receipt.sha256(),
                    receipt=receipt,
                    acknowledgement=acknowledgement,
                    approved_actor="dashboard-admin",
                    request_id="admin-request",
                )
        stored = await repository.get(captured.id)
        assert stored is not None
        assert stored.state == HttpBridgeRowlessRecoveryState.CAPTURED
        assert await session.scalar(select(AuditLog)) is None


@pytest.mark.asyncio
async def test_rowless_duplicate_capture_is_stable_and_singleton(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_id = "task-duplicate"
    digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )

    async def capture_with(
        repository: RowlessRecoveryRepository,
        *,
        anchor: str,
    ) -> RowlessRecoveryAuthoritySnapshot:
        return await repository.capture(
            api_key_scope="key-scope",
            session_key_kind="session_header",
            strong_session_hash=rowless_strong_session_hash("task_authority", digest),
            stale_anchor_hash=canonical_json_sha256({"anchor": anchor}),
            selected_account_intent="account-a",
            task_identity=task_id,
            session_identity=task_id,
            task_authority_digest=digest,
            facts=_facts(input_count=85),
        )

    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)
        first = await capture_with(repository, anchor="anchor-a")
        second = await capture_with(repository, anchor="anchor-b")
        assert second.id == first.id
        rows = (await session.scalars(select(HttpBridgeRowlessRecoveryAuthority))).all()
        assert [row.id for row in rows] == [first.id]

    async def capture_once(anchor: str) -> str:
        async with async_session_factory() as session:
            return (await capture_with(RowlessRecoveryRepository(session), anchor=anchor)).id

    assert await asyncio.gather(capture_once("anchor-c"), capture_once("anchor-d")) == [first.id, first.id]


@pytest.mark.asyncio
async def test_rowless_concurrent_first_captures_with_different_anchors_converge(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_id = "task-concurrent-different-anchors"
    digest = rowless_task_authority_digest(
        session_id=task_id,
        prompt_cache_key=task_id,
        thread_id=task_id,
    )

    async def capture(anchor: str) -> str:
        async with async_session_factory() as session:
            captured = await RowlessRecoveryRepository(session).capture(
                api_key_scope="key-scope",
                session_key_kind="session_header",
                strong_session_hash=rowless_strong_session_hash("task_authority", digest),
                stale_anchor_hash=canonical_json_sha256({"anchor": anchor}),
                selected_account_intent="account-a",
                task_identity=task_id,
                session_identity=task_id,
                task_authority_digest=digest,
                facts=_facts(input_count=85),
            )
            return captured.id

    authority_ids = await asyncio.gather(capture("anchor-a"), capture("anchor-b"))
    assert authority_ids[0] == authority_ids[1]
    async with async_session_factory() as session:
        rows = (await session.scalars(select(HttpBridgeRowlessRecoveryAuthority))).all()
        assert [row.id for row in rows] == [authority_ids[0]]


@pytest.mark.asyncio
async def test_rowless_preflight_cas_has_exactly_one_winner_and_crash_stays_unknown(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-concurrent",
            input_count=85,
        )
        receipt = _receipt(
            task_id="task-concurrent",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id=None,
        )

    async def claim(request_id: str) -> str:
        async with async_session_factory() as session:
            try:
                await RowlessRecoveryRepository(session).claim_dispatch_preflight(
                    authority_id=captured.id,
                    generation=captured.generation,
                    request_id=request_id,
                    wire_request_fingerprint="d" * 64,
                    task_authority_digest=captured.captured_task_authority_digest,
                )
            except RowlessRecoveryStateError:
                return "lost"
            return "won"

    assert sorted(await asyncio.gather(claim("request-1"), claim("request-2"))) == ["lost", "won"]
    async with async_session_factory() as session:
        stored = await RowlessRecoveryRepository(session).get(captured.id)
        assert stored is not None
        assert stored.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        assert stored.replacement_session_id is None
        assert stored.dispatch_send_started_at is None
        assert await session.scalar(select(HttpBridgeRecoveryAttemptRecord)) is None


@pytest.mark.asyncio
async def test_rowless_local_setup_failure_can_restore_preflight_before_replacement(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-setup-failure",
            input_count=85,
        )
        receipt = _receipt(
            task_id="task-setup-failure",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id=None,
        )
        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-setup-failure",
            wire_request_fingerprint="e" * 64,
            task_authority_digest=captured.captured_task_authority_digest,
        )
        assert await repository.rollback_preflight_setup_failure(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-setup-failure",
            wire_request_fingerprint="e" * 64,
        )
        stored = await repository.get(captured.id)
        assert stored is not None
        assert stored.state == HttpBridgeRowlessRecoveryState.APPROVED
        assert stored.dispatch_request_id is None


@pytest.mark.asyncio
async def test_rowless_retention_purges_expired_capture_but_never_consumed_tombstone(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    task_ids = ("expired-captured", "expired-consumed", "approved-kept", "unknown-kept")
    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)
        captured_ids: dict[str, str] = {}
        for task_id in task_ids:
            digest = rowless_task_authority_digest(
                session_id=task_id,
                prompt_cache_key=task_id,
                thread_id=task_id,
            )
            captured = await repository.capture(
                api_key_scope="retention-scope",
                session_key_kind="session_header",
                strong_session_hash=rowless_strong_session_hash("task_authority", digest),
                stale_anchor_hash=canonical_json_sha256({"anchor": task_id}),
                selected_account_intent="account-a",
                task_identity=task_id,
                session_identity=task_id,
                task_authority_digest=digest,
                facts=_facts(input_count=85),
            )
            captured_ids[task_id] = captured.id

        old = datetime.now(timezone.utc) - timedelta(seconds=ROWLESS_RECOVERY_CAPTURED_RETENTION_SECONDS + 60)
        await session.execute(
            update(HttpBridgeRowlessRecoveryAuthority)
            .where(HttpBridgeRowlessRecoveryAuthority.id.in_(captured_ids.values()))
            .values(updated_at=old)
        )
        await session.execute(
            update(HttpBridgeRowlessRecoveryAuthority)
            .where(HttpBridgeRowlessRecoveryAuthority.id == captured_ids["expired-consumed"])
            .values(state=HttpBridgeRowlessRecoveryState.CONSUMED, consumed_at=old)
        )
        await session.execute(
            update(HttpBridgeRowlessRecoveryAuthority)
            .where(HttpBridgeRowlessRecoveryAuthority.id == captured_ids["approved-kept"])
            .values(state=HttpBridgeRowlessRecoveryState.APPROVED)
        )
        await session.execute(
            update(HttpBridgeRowlessRecoveryAuthority)
            .where(HttpBridgeRowlessRecoveryAuthority.id == captured_ids["unknown-kept"])
            .values(state=HttpBridgeRowlessRecoveryState.UNKNOWN)
        )
        await session.commit()

        now = datetime.now(timezone.utc)
        deleted = await repository.purge_expired_audit_rows(
            captured_cutoff=now - timedelta(seconds=ROWLESS_RECOVERY_CAPTURED_RETENTION_SECONDS),
        )
        assert deleted == {"captured": 1}
        remaining = set((await session.scalars(select(HttpBridgeRowlessRecoveryAuthority.id))).all())
        assert remaining == {
            captured_ids["expired-consumed"],
            captured_ids["approved-kept"],
            captured_ids["unknown-kept"],
        }
        consumed_task = "expired-consumed"
        consumed_digest = rowless_task_authority_digest(
            session_id=consumed_task,
            prompt_cache_key=consumed_task,
            thread_id=consumed_task,
        )
        repeated = await repository.capture(
            api_key_scope="retention-scope",
            session_key_kind="session_header",
            strong_session_hash=rowless_strong_session_hash("task_authority", consumed_digest),
            stale_anchor_hash=canonical_json_sha256({"anchor": consumed_task}),
            selected_account_intent="account-a",
            task_identity=consumed_task,
            session_identity=consumed_task,
            task_authority_digest=consumed_digest,
            facts=_facts(input_count=85),
        )
        assert repeated.id == captured_ids["expired-consumed"]
        assert repeated.state == HttpBridgeRowlessRecoveryState.CONSUMED
        assert len((await session.scalars(select(HttpBridgeRowlessRecoveryAuthority))).all()) == 3


def test_rowless_exact_three_sanitized_incident_shapes_are_self_contained_and_exact() -> None:
    from tests.unit.test_replay_safety import _rehydrate_sanitized_pending_settlement_shapes

    cases = _rehydrate_sanitized_pending_settlement_shapes()
    assert [stored_count for _, stored_count, _ in cases] == [85, 288, 194]
    for items, _, _ in cases:
        payload = ResponsesRequest.model_validate(
            {
                "model": "gpt-5.1",
                "instructions": "sanitized fixture",
                "previous_response_id": "resp_stale_fixture",
                "prompt_cache_key": "fixture-task",
                "input": items,
            }
        )
        facts = build_rowless_recovery_capture_facts(payload)
        assert facts is not None
        assert facts.self_contained
        assert facts.account_neutral
        assert facts.unresolved_count == 0
        assert (
            approved_rowless_recovery_projection(
                payload,
                captured_input_item_count=facts.input_item_count,
                captured_input_fingerprint=facts.input_fingerprint,
                non_input_contract_fingerprint=facts.contract_fingerprint,
                direct_call_ledger_digest=facts.direct_call_ledger_digest,
                projected_payload_fingerprint=facts.projected_payload_fingerprint,
            )
            == facts.projected_input
        )
        assert (
            approved_rowless_recovery_projection(
                payload,
                captured_input_item_count=facts.input_item_count,
                captured_input_fingerprint=facts.input_fingerprint,
                non_input_contract_fingerprint=facts.contract_fingerprint,
                direct_call_ledger_digest=facts.direct_call_ledger_digest,
                projected_payload_fingerprint="f" * 64,
            )
            is None
        )


def test_automatic_rowless_proof_requires_retained_output_before_fresh_followup() -> None:
    common = {
        "model": "gpt-5.1",
        "instructions": "sanitized fixture",
        "previous_response_id": "resp_stale_fixture",
        "prompt_cache_key": "fixture-task",
    }
    incremental = build_rowless_recovery_capture_facts(
        ResponsesRequest.model_validate(
            {
                **common,
                "input": [{"role": "user", "content": "continue"}],
            }
        )
    )
    complete = build_rowless_recovery_capture_facts(
        ResponsesRequest.model_validate(
            {
                **common,
                "input": [
                    {"role": "user", "content": "original"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "original result"}],
                    },
                    {"role": "user", "content": "continue"},
                ],
            }
        )
    )
    compacted = build_rowless_recovery_capture_facts(
        ResponsesRequest.model_validate(
            {
                **common,
                "input": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "compacted result"}],
                    },
                    {"role": "user", "content": "continue"},
                ],
            }
        )
    )

    assert incremental is not None
    assert incremental.self_contained
    assert not incremental.retains_prior_output
    assert complete is not None
    assert complete.self_contained
    assert complete.retains_prior_output
    assert compacted is not None
    assert compacted.self_contained
    assert compacted.retains_prior_output


def test_rowless_capture_accepts_canonical_root_retry_chain_for_automatic_recovery() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "sanitized fixture",
            "previous_response_id": "resp_stale_fixture",
            "prompt_cache_key": "fixture-task",
            "input": [
                {"role": "user", "content": "original"},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "original result"}],
                },
                {
                    "type": "message",
                    "id": "msg_00000000-0000-4000-8000-000000000021",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "first failed retry"}],
                },
                {
                    "type": "message",
                    "id": "msg_00000000-0000-4000-8000-000000000022",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "bounded retry context"}],
                },
                {
                    "type": "message",
                    "id": "msg_00000000-0000-4000-8000-000000000023",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "live retry"}],
                },
            ],
        }
    )

    facts = build_rowless_recovery_capture_facts(payload)

    assert facts is not None
    assert facts.unresolved_count == 0
    assert facts.self_contained
    assert facts.account_neutral
    assert facts.retains_prior_output


def test_staged_settled_retry_requires_lite_bundle_to_preserve_developer_order() -> None:
    from tests.unit.test_replay_safety import _staged_settled_custom_tool_retry_chain

    lite_input = _staged_settled_custom_tool_retry_chain()
    common = {
        "model": "gpt-5.6-sol",
        "instructions": "sanitized fixture",
        "previous_response_id": "resp_stale_fixture",
        "prompt_cache_key": "fixture-task",
    }
    lite_facts = build_rowless_recovery_capture_facts(ResponsesRequest.model_validate({**common, "input": lite_input}))
    non_lite_facts = build_rowless_recovery_capture_facts(
        ResponsesRequest.model_validate({**common, "input": lite_input[1:]})
    )
    malformed_lite_input = copy.deepcopy(lite_input)
    duplicate_developer = copy.deepcopy(malformed_lite_input[3])
    cast(dict[str, object], duplicate_developer)["id"] = "msg_00000000-0000-4000-8000-000000000299"
    malformed_lite_input.insert(4, duplicate_developer)
    malformed_lite_facts = build_rowless_recovery_capture_facts(
        ResponsesRequest.model_validate({**common, "input": malformed_lite_input})
    )
    misplaced_lite_input = copy.deepcopy(lite_input)
    misplaced_lite_input.insert(0, {"type": "message", "role": "user", "content": "non-Lite prefix"})
    misplaced_lite_facts = build_rowless_recovery_capture_facts(
        ResponsesRequest.model_validate({**common, "input": misplaced_lite_input})
    )

    assert lite_facts is not None
    assert lite_facts.retains_prior_output
    assert non_lite_facts is not None
    assert not non_lite_facts.retains_prior_output
    assert malformed_lite_facts is not None
    assert not malformed_lite_facts.retains_prior_output
    assert misplaced_lite_facts is not None
    assert not misplaced_lite_facts.retains_prior_output


def test_rowless_capture_normalizes_only_exact_empty_output_and_encrypted_agent_transport_parts() -> None:
    items: list[object] = [
        {
            "type": "custom_tool_call",
            "call_id": "call-1",
            "name": "fixture_tool",
            "input": "{}",
            "status": "completed",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call-1",
            "output": [
                {"type": "input_text", "text": "settled"},
                {"type": "input_text", "text": ""},
            ],
        },
        {
            "type": "function_call",
            "call_id": "call-2",
            "namespace": "collaboration",
            "name": "send_message",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call-2", "output": "settled"},
        {
            "type": "agent_message",
            "id": "amsg_00000000-0000-4000-8000-000000000001",
            "author": "/root/worker",
            "recipient": "/root",
            "content": [
                {"type": "input_text", "text": "completed"},
                {"type": "encrypted_content", "encrypted_content": "opaque-response-state"},
            ],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "00000000-0000-4000-8000-000000000002",
                "create_time": 1.0,
            },
        },
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "sanitized fixture",
            "previous_response_id": "resp_stale_fixture",
            "prompt_cache_key": "fixture-task",
            "input": items,
        }
    )

    facts = build_rowless_recovery_capture_facts(payload)

    assert facts is not None
    assert facts.self_contained
    assert facts.account_neutral
    assert facts.retains_prior_output
    assert facts.unresolved_count == 0
    projected_output = next(
        cast(dict[str, object], item)
        for item in facts.projected_input
        if isinstance(item, dict) and item.get("type") == "custom_tool_call_output"
    )
    assert projected_output["output"] == [{"type": "input_text", "text": "settled"}]
    projected_function_call = next(
        cast(dict[str, object], item)
        for item in facts.projected_input
        if isinstance(item, dict) and item.get("type") == "function_call"
    )
    assert projected_function_call["namespace"] == "collaboration"
    projected_agent = next(
        cast(dict[str, object], item)
        for item in facts.projected_input
        if isinstance(item, dict) and item.get("type") == "agent_message"
    )
    assert "id" not in projected_agent
    assert projected_agent["content"] == [{"type": "input_text", "text": "completed"}]
    assert (
        approved_rowless_recovery_projection(
            payload,
            captured_input_item_count=facts.input_item_count,
            captured_input_fingerprint=facts.input_fingerprint,
            non_input_contract_fingerprint=facts.contract_fingerprint,
            direct_call_ledger_digest=facts.direct_call_ledger_digest,
            projected_payload_fingerprint=facts.projected_payload_fingerprint,
        )
        == facts.projected_input
    )


def test_rowless_capture_accepts_compacted_agent_output_at_index_zero() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "sanitized fixture",
            "previous_response_id": "resp_stale_fixture",
            "prompt_cache_key": "fixture-task",
            "input": [
                {
                    "type": "agent_message",
                    "id": "amsg_00000000-0000-4000-8000-000000000001",
                    "author": "/root/worker",
                    "recipient": "/root",
                    "content": [{"type": "input_text", "text": "completed"}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
            ],
        }
    )

    facts = build_rowless_recovery_capture_facts(payload)

    assert facts is not None
    assert facts.self_contained
    assert facts.account_neutral
    assert facts.retains_prior_output


@pytest.mark.parametrize(
    "mutate",
    (
        lambda items: cast(
            dict[str, object], cast(list[object], cast(dict[str, object], items[1])["output"])[1]
        ).update({"unexpected": "drift"}),
        lambda items: cast(
            dict[str, object], cast(list[object], cast(dict[str, object], items[2])["content"])[1]
        ).update({"unexpected": "drift"}),
        lambda items: cast(dict[str, object], items[2]).update(
            {
                "content": [
                    {"type": "encrypted_content", "encrypted_content": "opaque-response-state"},
                    {"type": "input_text", "text": "completed"},
                ]
            }
        ),
    ),
)
def test_rowless_transport_artifact_normalization_rejects_semantic_or_shape_drift(mutate) -> None:
    items: list[object] = [
        {
            "type": "custom_tool_call",
            "call_id": "call-1",
            "name": "fixture_tool",
            "input": "{}",
            "status": "completed",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call-1",
            "output": [
                {"type": "input_text", "text": "settled"},
                {"type": "input_text", "text": ""},
            ],
        },
        {
            "type": "agent_message",
            "id": "amsg_00000000-0000-4000-8000-000000000001",
            "author": "/root/worker",
            "recipient": "/root",
            "content": [
                {"type": "input_text", "text": "completed"},
                {"type": "encrypted_content", "encrypted_content": "opaque-response-state"},
            ],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "00000000-0000-4000-8000-000000000002",
                "create_time": 1.0,
            },
        },
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]
    mutate(items)
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.6-sol",
            "instructions": "sanitized fixture",
            "previous_response_id": "resp_stale_fixture",
            "prompt_cache_key": "fixture-task",
            "input": items,
        }
    )

    facts = build_rowless_recovery_capture_facts(payload)
    assert facts is None or not facts.self_contained or not facts.account_neutral


def test_rowless_capture_rejects_transformable_external_image_url() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.1",
            "instructions": "Inspect the supplied image.",
            "previous_response_id": "resp_stale",
            "prompt_cache_key": "root-task",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "inspect"},
                        {"type": "input_image", "image_url": "https://example.invalid/dynamic.png"},
                    ],
                }
            ],
        }
    )
    assert build_rowless_recovery_capture_facts(payload) is None


def test_rowless_agent_message_proof_rejects_shape_metadata_and_boundary_drift() -> None:
    from tests.unit.test_replay_safety import _rehydrate_sanitized_pending_settlement_shapes

    items, _, _ = _rehydrate_sanitized_pending_settlement_shapes()[1]
    agent_index = next(
        index for index, item in enumerate(items) if isinstance(item, dict) and item.get("type") == "agent_message"
    )
    malformed_variants = []

    unknown_field = copy.deepcopy(items)
    cast(dict[str, object], unknown_field[agent_index])["unexpected"] = "drift"
    malformed_variants.append(unknown_field)

    metadata_drift = copy.deepcopy(items)
    cast(dict[str, object], metadata_drift[agent_index])["internal_chat_message_metadata_passthrough"] = {
        "turn_id": "not-a-uuid"
    }
    malformed_variants.append(metadata_drift)

    missing_fresh_user_boundary = copy.deepcopy(items[: agent_index + 1])
    malformed_variants.append(missing_fresh_user_boundary)

    equal_agent_paths = copy.deepcopy(items)
    equal_agent = cast(dict[str, object], equal_agent_paths[agent_index])
    equal_agent["recipient"] = equal_agent["author"]
    malformed_variants.append(equal_agent_paths)

    recipient_path_escape = copy.deepcopy(items)
    cast(dict[str, object], recipient_path_escape[agent_index])["recipient"] = "/root/../escape"
    malformed_variants.append(recipient_path_escape)

    output_bearing_agent = copy.deepcopy(items)
    cast(dict[str, object], output_bearing_agent[agent_index])["content"] = [{"type": "output_text", "text": "drift"}]
    malformed_variants.append(output_bearing_agent)

    invalid_agent_id = copy.deepcopy(items)
    cast(dict[str, object], invalid_agent_id[agent_index])["id"] = "amsg_not-a-uuid"
    malformed_variants.append(invalid_agent_id)

    pending_before_agent = copy.deepcopy(items)
    pending_before_agent.insert(
        agent_index,
        {
            "type": "custom_tool_call",
            "call_id": "dangling-before-agent",
            "name": "fixture_tool",
            "input": "{}",
            "status": "completed",
        },
    )
    malformed_variants.append(pending_before_agent)

    orphan_output = copy.deepcopy(items)
    orphan_output.insert(
        agent_index,
        {
            "type": "custom_tool_call_output",
            "call_id": "orphan-output",
            "output": "fixture-output",
        },
    )
    malformed_variants.append(orphan_output)

    dangling_after_agent = copy.deepcopy(items)
    dangling_after_agent.insert(
        agent_index + 1,
        {
            "type": "function_call",
            "call_id": "dangling-after-agent",
            "name": "fixture_tool",
            "arguments": "{}",
        },
    )
    malformed_variants.append(dangling_after_agent)

    for variant_index, malformed in enumerate(malformed_variants):
        payload = ResponsesRequest.model_validate(
            {
                "model": "gpt-5.1",
                "instructions": "sanitized fixture",
                "previous_response_id": "resp_stale_fixture",
                "prompt_cache_key": "fixture-task",
                "input": malformed,
            }
        )
        facts = build_rowless_recovery_capture_facts(payload)
        assert facts is None or not facts.self_contained or not facts.account_neutral, f"variant {variant_index}"


@pytest.mark.asyncio
async def test_rowless_task_authority_isolates_children_sharing_root_session_and_prompt(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    root_session = "shared-root-session"
    child_a = "child-thread-a"
    child_b = "child-thread-b"
    digest_a = rowless_task_authority_digest(
        session_id=root_session,
        prompt_cache_key=root_session,
        thread_id=child_a,
    )
    digest_b = rowless_task_authority_digest(
        session_id=root_session,
        prompt_cache_key=root_session,
        thread_id=child_b,
    )
    assert digest_a != digest_b

    async with async_session_factory() as session:
        repository = RowlessRecoveryRepository(session)
        captured_a = await repository.capture(
            api_key_scope="shared-scope",
            session_key_kind="session_header",
            strong_session_hash=rowless_strong_session_hash("task_authority", digest_a),
            stale_anchor_hash="f" * 64,
            selected_account_intent="account-a",
            task_identity=child_a,
            session_identity=root_session,
            task_authority_digest=digest_a,
            facts=_facts(input_count=85),
        )
        captured_b = await repository.capture(
            api_key_scope="shared-scope",
            session_key_kind="session_header",
            strong_session_hash=rowless_strong_session_hash("task_authority", digest_b),
            stale_anchor_hash="f" * 64,
            selected_account_intent="account-a",
            task_identity=child_b,
            session_identity=root_session,
            task_authority_digest=digest_b,
            facts=_facts(input_count=85),
        )
        assert captured_a.id != captured_b.id
        challenge = await repository.issue_challenge(
            authority_id=captured_a.id,
            generation=captured_a.generation,
        )
        wrong_child_receipt = replace(
            _receipt(
                task_id=child_b,
                strong_session_hash=captured_a.strong_session_hash,
                full_ledger_pairs=93,
            ),
            session_identity=root_session,
            task_authority_digest=digest_b,
        )
        with pytest.raises(RowlessRecoveryStateError, match="checkpoint_receipt_contract_mismatch"):
            await repository.approve(
                authority_id=captured_a.id,
                generation=captured_a.generation,
                challenge=challenge.challenge,
                declared_receipt_sha256=wrong_child_receipt.sha256(),
                receipt=wrong_child_receipt,
                acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
                approved_actor="dashboard-admin",
                request_id=None,
            )


@pytest.mark.asyncio
async def test_rowless_terminal_persistence_failure_rolls_back_anchor_and_consumption(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch,
) -> None:
    async with async_session_factory() as session:
        rejected_anchor = "resp-terminal-stale"
        replacement = HttpBridgeSessionRecord(
            session_key_kind="session_header",
            session_key_value="task-terminal-rollback",
            session_key_hash=canonical_json_sha256("task-terminal-rollback"),
            api_key_scope="key-scope",
            account_id="account-a",
            owner_instance_id="instance-a",
            owner_epoch=7,
            latest_response_id=rejected_anchor,
            recovery_required_anchor_hash=durable_bridge_hash(rejected_anchor),
            recovery_required_account_id="account-a",
            recovery_required_at=datetime.now(timezone.utc),
        )
        session.add(replacement)
        await session.commit()
        replacement_id = replacement.id
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-terminal-rollback",
            input_count=85,
            stale_anchor_hash=durable_bridge_hash(rejected_anchor),
            origin_marker_session_id=replacement_id,
        )
        receipt = _receipt(
            task_id="task-terminal-rollback",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id=None,
        )
        await repository.claim_dispatch_preflight(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-terminal",
            wire_request_fingerprint="9" * 64,
            task_authority_digest=captured.captured_task_authority_digest,
        )
        await repository.claim_dispatch(
            authority_id=captured.id,
            generation=captured.generation,
            replacement_session_id=replacement_id,
            request_id="request-terminal",
            wire_request_fingerprint="9" * 64,
            model="gpt-5.6",
            task_authority_digest=captured.captured_task_authority_digest,
        )
        assert await repository.mark_dispatch_send_started(
            authority_id=captured.id,
            generation=captured.generation,
            request_id="request-terminal",
            wire_request_fingerprint="9" * 64,
        )

        async def fail_alias(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("injected terminal persistence failure")

        monkeypatch.setattr(DurableBridgeRepository, "_execute_alias_upsert", fail_alias)
        with pytest.raises(RuntimeError, match="injected terminal persistence failure"):
            await repository.settle_completed(
                authority_id=captured.id,
                generation=captured.generation,
                replacement_session_id=replacement_id,
                owner_instance_id="instance-a",
                owner_epoch=7,
                request_id="request-terminal",
                response_id="resp-terminal",
                input_item_count=captured.captured_input_item_count,
                input_full_fingerprint=captured.captured_input_fingerprint,
                pending_tool_calls={},
            )
        await session.rollback()
        stored = await repository.get(captured.id)
        assert stored is not None
        assert stored.state == HttpBridgeRowlessRecoveryState.UNKNOWN
        durable_replacement = await session.get(HttpBridgeSessionRecord, replacement_id)
        assert durable_replacement is not None
        assert durable_replacement.latest_response_id == rejected_anchor
        assert durable_replacement.recovery_required_anchor_hash == durable_bridge_hash(rejected_anchor)
        assert durable_replacement.recovery_required_account_id == "account-a"
        assert durable_replacement.recovery_required_attempt_fingerprint is not None
        attempt = await session.scalar(select(HttpBridgeRecoveryAttemptRecord))
        assert attempt is not None
        assert attempt.state.value == "unknown"
        assert attempt.response_id is None


@pytest.mark.asyncio
async def test_rowless_state_counts_expose_non_captured_image_rollback_floor(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    async with async_session_factory() as session:
        repository, captured, challenge = await _capture_and_challenge(
            session,
            task_id="task-rollback-floor",
            input_count=85,
        )
        assert await repository.authority_state_counts() == {
            "captured": 1,
            "approved": 0,
            "unknown": 0,
            "consumed": 0,
        }
        receipt = _receipt(
            task_id="task-rollback-floor",
            strong_session_hash=captured.strong_session_hash,
            full_ledger_pairs=93,
        )
        await repository.approve(
            authority_id=captured.id,
            generation=captured.generation,
            challenge=challenge,
            declared_receipt_sha256=receipt.sha256(),
            receipt=receipt,
            acknowledgement=ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT,
            approved_actor="dashboard-admin",
            request_id=None,
        )
        counts = await repository.authority_state_counts()
        assert counts["captured"] == 0
        assert counts["approved"] == 1
