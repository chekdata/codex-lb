"""Content-free proofs for an operator-acknowledged rowless semantic rebase."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    normalize_responses_input_for_rowless_replay,
    project_responses_input_for_account_neutral_fresh_replay,
    responses_direct_call_ledger_summary,
    responses_input_items_are_self_contained_rowless_replay,
    responses_input_retains_prior_output_and_fresh_followup,
    responses_input_retains_prior_output_and_root_retry_chain,
    responses_payload_is_account_neutral_fresh_replay,
)

ROWLESS_SEMANTIC_REBASE_ACKNOWLEDGEMENT = "operator_acknowledged_semantic_rebase"
ROWLESS_FORWARDING_PAYLOAD_SCHEMA = "qk_http_bridge_rowless_forwarding_payload_v1"
ROWLESS_AUTHORIZATION_MODE_AUTOMATIC = "automatic_live_request"
ROWLESS_AUTHORIZATION_MODE_OPERATOR = "operator_checkpoint"


@dataclass(frozen=True, slots=True)
class RowlessRecoveryCaptureFacts:
    input_item_count: int
    input_fingerprint: str
    contract_fingerprint: str
    direct_call_ledger_digest: str
    projected_payload_fingerprint: str
    actual_wire_fingerprint: str
    unresolved_count: int
    projected_input: list[JsonValue]
    self_contained: bool
    account_neutral: bool
    retains_prior_output: bool


@dataclass(frozen=True, slots=True)
class RowlessRecoveryCaptureIntent:
    api_key_scope: str
    session_key_kind: str
    strong_session_hash: str
    task_authority_digest: str
    task_identity: str
    session_identity: str
    facts: RowlessRecoveryCaptureFacts
    automatic_live_recovery: bool = False


def canonical_json_sha256(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rowless_strong_session_hash(session_key_kind: str, session_key_value: str) -> str:
    return canonical_json_sha256({"kind": session_key_kind, "value": session_key_value})


def rowless_task_authority_digest(
    *,
    session_id: str,
    prompt_cache_key: str,
    thread_id: str,
) -> str:
    """Bind the stable Codex task identity independently of turn-state routing."""

    payload = bytearray(b"qk-http-bridge-task-authority-v1\0")
    for tag, value in (
        ("session-id", session_id),
        ("prompt_cache_key", prompt_cache_key),
        ("thread-id", thread_id),
    ):
        tag_bytes = tag.encode("utf-8")
        value_bytes = value.encode("utf-8")
        payload.extend(len(tag_bytes).to_bytes(2, "big"))
        payload.extend(tag_bytes)
        payload.extend(len(value_bytes).to_bytes(4, "big"))
        payload.extend(value_bytes)
    return hashlib.sha256(payload).hexdigest()


def responses_non_input_contract_fingerprint(payload: ResponsesRequest) -> str:
    contract = dict(payload.model_dump_for_forwarding())
    contract.pop("input", None)
    contract.pop("previous_response_id", None)
    return canonical_json_sha256(contract)


def rowless_forwarding_payload_fingerprint(payload: ResponsesRequest) -> str:
    """Bind the anchor-free logical payload and projection version."""

    return canonical_json_sha256(
        {
            "schema": ROWLESS_FORWARDING_PAYLOAD_SCHEMA,
            "payload": payload.model_dump_for_forwarding(),
        }
    )


def rowless_actual_wire_fingerprint(request_text: str) -> str:
    """Bind the exact serialized response.create bytes sent upstream."""

    digest = hashlib.sha256()
    digest.update(b"qk-http-bridge-rowless-actual-wire-v1\0")
    digest.update(request_text.encode("utf-8"))
    return digest.hexdigest()


def rowless_projected_actual_wire_fingerprint(
    request_text: str,
    projected_input: list[JsonValue],
) -> str:
    """Hash the exact transformed first wire after applying the approved rebase projection."""

    return rowless_actual_wire_fingerprint(rowless_projected_actual_wire_text(request_text, projected_input))


def rowless_projected_actual_wire_text(
    request_text: str,
    projected_input: list[JsonValue],
) -> str:
    """Return the exact anchor-free wire retained only by the live request."""

    payload = json.loads(request_text)
    if not isinstance(payload, dict):
        raise ValueError("rowless wire payload must be an object")
    payload["input"] = projected_input
    payload.pop("previous_response_id", None)
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _contains_transformable_external_image(value: JsonValue) -> bool:
    if isinstance(value, list):
        return any(_contains_transformable_external_image(item) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") == "input_image":
        image_url = value.get("image_url")
        if isinstance(image_url, str) and image_url.lower().startswith(("http://", "https://")):
            return True
    return any(_contains_transformable_external_image(item) for item in value.values())


def build_rowless_recovery_capture_facts(
    payload: ResponsesRequest,
    *,
    expected_session_identity: str | None = None,
    expected_task_identity: str | None = None,
) -> RowlessRecoveryCaptureFacts | None:
    if not isinstance(payload.input, list) or not payload.input:
        return None
    input_items = cast(list[JsonValue], payload.input)
    if _contains_transformable_external_image(input_items):
        return None
    ledger = responses_direct_call_ledger_summary(input_items)
    if ledger is None:
        return None
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=len(input_items),
    )
    evidence_projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=len(input_items),
        preserve_response_owned_agent_message_ids=True,
    )
    if projection is None or evidence_projection is None:
        return None
    projected_input = normalize_responses_input_for_rowless_replay(projection.input_items)
    evidence_input = normalize_responses_input_for_rowless_replay(evidence_projection.input_items)
    if projected_input is None or evidence_input is None:
        return None
    projected_payload = payload.model_copy(update={"input": projected_input, "previous_response_id": None})
    self_contained = responses_input_items_are_self_contained_rowless_replay(
        input_items,
        projected_input,
    )
    # The wire projection strips response-owned IDs. Keep them only in this
    # classification copy so retained agent output can still be proven.
    retains_prior_output = responses_input_retains_prior_output_and_fresh_followup(
        evidence_input
    ) or responses_input_retains_prior_output_and_root_retry_chain(input_items)
    account_neutral_input = [
        item for item in projected_input if not (isinstance(item, dict) and item.get("type") == "agent_message")
    ]
    account_neutral_payload = projected_payload.model_copy(update={"input": account_neutral_input})
    return RowlessRecoveryCaptureFacts(
        input_item_count=len(input_items),
        input_fingerprint=canonical_json_sha256(input_items),
        contract_fingerprint=responses_non_input_contract_fingerprint(payload),
        direct_call_ledger_digest=ledger.digest,
        projected_payload_fingerprint=rowless_forwarding_payload_fingerprint(projected_payload),
        actual_wire_fingerprint=rowless_forwarding_payload_fingerprint(projected_payload),
        unresolved_count=ledger.unresolved_count,
        projected_input=projected_input,
        self_contained=self_contained,
        account_neutral=(
            self_contained
            and responses_payload_is_account_neutral_fresh_replay(
                account_neutral_payload.to_replay_safety_payload(),
                expected_session_identity=expected_session_identity,
                expected_task_identity=expected_task_identity,
            )
        ),
        retains_prior_output=retains_prior_output,
    )


def approved_rowless_recovery_projection(
    payload: ResponsesRequest,
    *,
    captured_input_item_count: int,
    captured_input_fingerprint: str,
    non_input_contract_fingerprint: str,
    direct_call_ledger_digest: str,
    projected_payload_fingerprint: str,
) -> list[JsonValue] | None:
    """Return the one safe anchor-free projection or fail closed."""

    if not isinstance(payload.input, list):
        return None
    input_items = cast(list[JsonValue], payload.input)
    if captured_input_item_count <= 0 or len(input_items) != captured_input_item_count:
        return None
    if canonical_json_sha256(input_items[:captured_input_item_count]) != captured_input_fingerprint:
        return None
    if responses_non_input_contract_fingerprint(payload) != non_input_contract_fingerprint:
        return None
    ledger = responses_direct_call_ledger_summary(input_items[:captured_input_item_count])
    if ledger is None or ledger.unresolved_count != 0 or ledger.digest != direct_call_ledger_digest:
        return None
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=captured_input_item_count,
    )
    if projection is None:
        return None
    projected_input = normalize_responses_input_for_rowless_replay(projection.input_items)
    if projected_input is None or not responses_input_items_are_self_contained_rowless_replay(
        input_items,
        projected_input,
    ):
        return None
    projected_payload = payload.model_copy(update={"input": projected_input, "previous_response_id": None})
    if rowless_forwarding_payload_fingerprint(projected_payload) != projected_payload_fingerprint:
        return None
    return projected_input
