"""Content-free durable proofs for completed Responses output transitions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.core.openai.public_output import normalize_public_output_item
from app.core.types import JsonValue

RESPONSE_TRANSITION_MANIFEST_SCHEMA = "qk_http_bridge_response_transition_manifest_v1"
_MAX_MANIFEST_ITEMS = 4096
_SUPPORTED_OUTPUT_ITEM_TYPES = frozenset(
    {
        "agent_message",
        "apply_patch_call",
        "custom_tool_call",
        "function_call",
        "image_generation_call",
        "message",
        "reasoning",
        "tool_search_call",
        "tool_search_output",
        "web_search_call",
    }
)
_CLIENT_SETTLED_CALL_TYPES = frozenset(
    {
        "apply_patch_call",
        "custom_tool_call",
        "function_call",
    }
)
_SUPPORTED_MANIFEST_ITEM_KINDS = (_SUPPORTED_OUTPUT_ITEM_TYPES - {"message"}) | {"message:assistant"}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _item_kind(item: Mapping[str, JsonValue]) -> str | None:
    item_type = item.get("type")
    if not isinstance(item_type, str) or item_type not in _SUPPORTED_OUTPUT_ITEM_TYPES:
        return None
    if item_type != "message":
        return item_type
    role = item.get("role")
    if role != "assistant":
        return None
    phase = item.get("phase")
    if phase is not None and (not isinstance(phase, str) or not phase):
        return None
    return "message:assistant"


def pending_tool_calls_digest(pending_tool_calls: Mapping[str, str]) -> str:
    return _canonical_sha256(dict(sorted(pending_tool_calls.items())))


@dataclass(frozen=True, slots=True)
class ResponseTransitionManifestItem:
    kind: str
    fingerprint: str

    def canonical_payload(self) -> dict[str, str]:
        return {"fingerprint": self.fingerprint, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class ResponseTransitionManifest:
    response_id_hash: str
    terminal_status: str
    pending_tool_calls_digest: str
    items: tuple[ResponseTransitionManifestItem, ...]
    schema: str = RESPONSE_TRANSITION_MANIFEST_SCHEMA

    def canonical_payload(self) -> dict[str, JsonValue]:
        return {
            "items": [item.canonical_payload() for item in self.items],
            "pending_tool_calls_digest": self.pending_tool_calls_digest,
            "response_id_hash": self.response_id_hash,
            "schema": self.schema,
            "terminal_status": self.terminal_status,
        }

    @property
    def digest(self) -> str:
        return _canonical_sha256(self.canonical_payload())

    @property
    def item_kinds(self) -> tuple[str, ...]:
        return tuple(item.kind for item in self.items)


def build_response_transition_manifest(
    payload: Mapping[str, JsonValue] | None,
    *,
    pending_tool_calls: Mapping[str, str],
    normalize_for_public_contract: bool = False,
) -> ResponseTransitionManifest | None:
    """Build a bounded manifest without retaining response or tool content."""

    response = payload.get("response") if isinstance(payload, Mapping) else None
    if not isinstance(response, Mapping):
        return None
    response_id = response.get("id")
    terminal_status = response.get("status")
    output = response.get("output")
    if (
        not isinstance(response_id, str)
        or not response_id
        or terminal_status != "completed"
        or not isinstance(output, list)
        or not output
        or len(output) > _MAX_MANIFEST_ITEMS
    ):
        return None

    manifest_items: list[ResponseTransitionManifestItem] = []
    observed_client_calls: dict[str, str] = {}
    for raw_item in output:
        if not isinstance(raw_item, Mapping):
            return None
        item = dict(raw_item)
        replay_item = normalize_public_output_item(item) if normalize_for_public_contract else item
        if replay_item is None:
            return None
        kind = _item_kind(replay_item)
        if kind is None:
            return None
        item_type = item.get("type")
        if item_type in _CLIENT_SETTLED_CALL_TYPES:
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in observed_client_calls:
                return None
            observed_client_calls[call_id] = str(item_type)
        manifest_items.append(
            ResponseTransitionManifestItem(
                kind=kind,
                fingerprint=_canonical_sha256(replay_item),
            )
        )

    if observed_client_calls != dict(pending_tool_calls):
        return None
    return ResponseTransitionManifest(
        response_id_hash=sha256(response_id.encode("utf-8")).hexdigest(),
        terminal_status="completed",
        pending_tool_calls_digest=pending_tool_calls_digest(pending_tool_calls),
        items=tuple(manifest_items),
    )


def response_transition_manifest_item_matches(
    item: Mapping[str, JsonValue],
    expected: ResponseTransitionManifestItem,
) -> bool:
    return _item_kind(item) == expected.kind and _canonical_sha256(dict(item)) == expected.fingerprint


def encode_response_transition_manifest(manifest: ResponseTransitionManifest | None) -> str | None:
    if manifest is None:
        return None
    return _canonical_json(manifest.canonical_payload())


def decode_response_transition_manifest(value: str | None) -> ResponseTransitionManifest | None:
    if value is None:
        return None
    try:
        payload: Any = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != {
        "items",
        "pending_tool_calls_digest",
        "response_id_hash",
        "schema",
        "terminal_status",
    }:
        return None
    if (
        payload.get("schema") != RESPONSE_TRANSITION_MANIFEST_SCHEMA
        or payload.get("terminal_status") != "completed"
        or not _is_sha256(payload.get("response_id_hash"))
        or not _is_sha256(payload.get("pending_tool_calls_digest"))
    ):
        return None
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > _MAX_MANIFEST_ITEMS:
        return None
    items: list[ResponseTransitionManifestItem] = []
    for raw_item in raw_items:
        if (
            not isinstance(raw_item, dict)
            or set(raw_item) != {"fingerprint", "kind"}
            or not isinstance(raw_item.get("kind"), str)
            or raw_item["kind"] not in _SUPPORTED_MANIFEST_ITEM_KINDS
            or not _is_sha256(raw_item.get("fingerprint"))
        ):
            return None
        items.append(
            ResponseTransitionManifestItem(
                kind=raw_item["kind"],
                fingerprint=raw_item["fingerprint"],
            )
        )
    return ResponseTransitionManifest(
        response_id_hash=payload["response_id_hash"],
        terminal_status=payload["terminal_status"],
        pending_tool_calls_digest=payload["pending_tool_calls_digest"],
        items=tuple(items),
        schema=payload["schema"],
    )


def response_transition_manifest_matches_context(
    manifest: ResponseTransitionManifest,
    *,
    response_id: str,
    pending_tool_calls: Mapping[str, str],
) -> bool:
    return manifest.response_id_hash == sha256(
        response_id.encode("utf-8")
    ).hexdigest() and manifest.pending_tool_calls_digest == pending_tool_calls_digest(pending_tool_calls)


def match_response_transition_manifest_prefix(
    input_items: Sequence[JsonValue],
    *,
    stored_count: int,
    manifest: ResponseTransitionManifest,
) -> int | None:
    """Return the first item after an exact manifest-bound output prefix."""

    manifest_end = stored_count + len(manifest.items)
    if stored_count < 0 or manifest_end > len(input_items):
        return None
    for actual, expected in zip(input_items[stored_count:manifest_end], manifest.items, strict=True):
        if not isinstance(actual, Mapping) or not response_transition_manifest_item_matches(actual, expected):
            return None
    return manifest_end
