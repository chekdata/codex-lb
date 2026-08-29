from __future__ import annotations

import logging
from typing import Any

from app.modules.proxy._service.http_bridge.helpers import _log_http_bridge_event
from app.modules.proxy._service.http_bridge.service_stubs import _service_get_settings
from app.modules.proxy.affinity import _extract_model_class

logger = logging.getLogger(__name__)


def _log_durable_anchor_poison_clear_failed(session: Any, detail: str) -> None:
    if session.durable_session_id is None:
        return
    _log_http_bridge_event(
        "durable_anchor_poison_clear_failed",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        pending_count=len(session.pending_requests),
        detail=detail,
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
    )


async def _abandon_durable_http_bridge_continuity(
    service: Any,
    session: Any,
    *,
    detail: str = "repeated_zero_event_idle_timeout",
    settle_circuit: bool = False,
) -> bool:
    """Clear durable continuity while the failed session still owns its row.

    The write is fenced by the session owner epoch.  A failed or fenced clear
    is deliberately reported as ``False`` so callers keep the retry circuit
    open and do not treat cooldown expiry as proof that the poisoned anchor is
    gone.
    """
    if session.durable_session_id is None or session.durable_owner_epoch is None:
        session.anchor_poison_clear_failed = True
        return False
    try:
        cleared = await service._durable_bridge.rebind_session_account(
            session_id=session.durable_session_id,
            api_key_id=session.key.api_key_id,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            owner_epoch=session.durable_owner_epoch,
            account_id=session.account.id,
            clear_continuity=True,
        )
    except Exception:
        session.anchor_poison_clear_failed = True
        _log_durable_anchor_poison_clear_failed(session, detail)
        logger.warning("Failed to abandon poisoned HTTP bridge continuity", exc_info=True)
        return False
    if not cleared:
        session.anchor_poison_clear_failed = True
        _log_durable_anchor_poison_clear_failed(session, detail)
        logger.warning(
            "Durable bridge continuity clear was fenced before poisoned anchor retirement",
            extra={
                "session_id": session.durable_session_id,
                "account_id": session.account.id,
            },
        )
        return False

    session.anchor_poison_cleared = True
    session.anchor_poison_clear_failed = False
    _log_http_bridge_event(
        "durable_anchor_poisoned",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        detail=detail,
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
    )
    # The anchor is gone, so the circuit can be settled as the cause-removal
    # step.  Keep this after the fenced continuity write: if circuit cleanup
    # itself is unavailable, the durable poison row remains conservative and a
    # later healthy response can still clear it.
    if settle_circuit:
        await service._clear_http_bridge_retry_circuit(session, settle_unfenced=True)
    return True
