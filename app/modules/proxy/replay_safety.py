"""Fail-closed checks for moving a retained Responses request between accounts."""

from __future__ import annotations

import json
import math
import re
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import UUID

from app.core.openai.requests import extract_input_file_ids
from app.core.types import JsonValue

_TOOL_CALL_TYPE_BY_OUTPUT_TYPE = {
    "function_call_output": "function_call",
    "custom_tool_call_output": "custom_tool_call",
    "apply_patch_call_output": "apply_patch_call",
}
_TOOL_CALL_TYPES = frozenset(_TOOL_CALL_TYPE_BY_OUTPUT_TYPE.values())
_ACCOUNT_NEUTRAL_REPLAY_OMITTED_ITEM_TYPES = frozenset(
    {"reasoning", "tool_search_call", "tool_search_output", "web_search_call"}
)
_INTERNAL_CHAT_MESSAGE_METADATA_FIELD = "internal_chat_message_metadata_passthrough"
_ACCOUNT_NEUTRAL_INTERNAL_CHAT_MESSAGE_METADATA_FIELDS = frozenset({"turn_id"})
_ACCOUNT_NEUTRAL_TOOL_TYPES = frozenset(
    {"custom", "function", "namespace", "tool_search", "web_search", "web_search_preview"}
)
_ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS = {
    "custom": frozenset({"defer_loading", "description", "format", "name", "type"}),
    "function": frozenset({"defer_loading", "description", "name", "parameters", "strict", "type"}),
    "namespace": frozenset({"description", "name", "tools", "type"}),
    "tool_search": frozenset({"description", "execution", "parameters", "type"}),
    "web_search": frozenset({"filters", "search_context_size", "type", "user_location"}),
    "web_search_preview": frozenset({"filters", "search_context_size", "type", "user_location"}),
}
_ACCOUNT_NEUTRAL_TOOL_CHOICE_STRINGS = frozenset({"auto", "none", "required"})
_ACCOUNT_NEUTRAL_WEB_SEARCH_CONTEXT_SIZES = frozenset({"high", "low", "medium"})
_ACCOUNT_NEUTRAL_WEB_SEARCH_FILTER_FIELDS = frozenset({"allowed_domains"})
_ACCOUNT_NEUTRAL_WEB_SEARCH_LOCATION_FIELDS = frozenset({"city", "country", "region", "timezone", "type"})
_ACCOUNT_NEUTRAL_MESSAGE_ROLES = frozenset({"assistant", "developer", "system", "user"})
_RESPONSE_OWNED_AGENT_MESSAGE_FIELDS = frozenset(
    {"author", "content", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "recipient", "type"}
)
_TRANSPORT_RESPONSE_OWNED_AGENT_MESSAGE_FIELDS = frozenset({"author", "content", "id", "recipient", "type"})
_RESPONSE_OWNED_AGENT_MESSAGE_METADATA_FIELDS = frozenset({"create_time", "turn_id"})
_RESPONSE_OWNED_USER_MESSAGE_FIELDS = frozenset(
    {"content", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "role", "type"}
)
_TRANSPORT_RESPONSE_OWNED_USER_MESSAGE_FIELDS = frozenset({"content", "id", "role", "type"})
# Agent paths are produced by the collaboration runtime, whose root is
# literally ``/root`` and whose task-name segments are restricted to lowercase
# letters, digits, and underscores.  This is replay authority, so accepting a
# merely path-shaped client string would be too broad.
_AGENT_PATH_PATTERN = re.compile(r"^/root(?:/[a-z0-9_]+)*$")
AbandonedPendingBoundaryRejectionReason = Literal[
    "stored_prefix_invalid",
    "pending_call_manifest_missing",
    "boundary_reasoning_shape_invalid",
    "boundary_agent_message_shape_invalid",
    "followup_missing",
    "followup_shape_invalid",
    "developer_message_shape_invalid",
    "developer_message_sequence_invalid",
    "pending_call_conflict",
    "projection_failed",
    "direct_call_prefix_state_invalid",
    "projected_boundary_invalid",
    "projected_followup_invalid",
]
_ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES = frozenset(
    {
        "additional_tools",
        "apply_patch_call",
        "apply_patch_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "function_call",
        "function_call_output",
        "input_file",
        "input_image",
        "input_text",
        "message",
    }
)
_ACCOUNT_NEUTRAL_MESSAGE_CONTENT_TYPES = frozenset(
    {"input_file", "input_image", "input_text", "output_text", "refusal", "text"}
)
_ACCOUNT_NEUTRAL_MESSAGE_FIELDS = frozenset(
    {"content", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "phase", "role", "status", "type"}
)
_ACCOUNT_NEUTRAL_CONTENT_FIELDS = {
    "input_file": frozenset({"file_data", "file_id", "file_url", "filename", "type"}),
    "input_image": frozenset({"detail", "file_id", "image_url", "type"}),
    "input_text": frozenset({"text", "type"}),
    "output_text": frozenset({"text", "type"}),
    "refusal": frozenset({"refusal", "type"}),
    "text": frozenset({"text", "type"}),
}
_ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS = {
    "additional_tools": frozenset({"role", "tools", "type"}),
    "apply_patch_call": frozenset(
        {
            "call_id",
            "caller",
            "id",
            "input",
            _INTERNAL_CHAT_MESSAGE_METADATA_FIELD,
            "operation",
            "patch",
            "status",
            "type",
        }
    ),
    "apply_patch_call_output": frozenset(
        {"call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "output", "status", "type"}
    ),
    "custom_tool_call": frozenset(
        {"call_id", "caller", "id", "input", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "name", "status", "type"}
    ),
    "custom_tool_call_output": frozenset(
        {"call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "output", "status", "type"}
    ),
    "function_call": frozenset(
        {
            "arguments",
            "call_id",
            "caller",
            "id",
            _INTERNAL_CHAT_MESSAGE_METADATA_FIELD,
            "name",
            "namespace",
            "status",
            "type",
        }
    ),
    "function_call_output": frozenset(
        {"call_id", "caller", "id", _INTERNAL_CHAT_MESSAGE_METADATA_FIELD, "output", "status", "type"}
    ),
}
_ACCOUNT_NEUTRAL_ITEM_STATUSES = frozenset({"completed", "failed"})
_ACCOUNT_NEUTRAL_APPLY_PATCH_OPERATION_FIELDS = {
    "create_file": frozenset({"diff", "path", "type"}),
    "delete_file": frozenset({"path", "type"}),
    "update_file": frozenset({"diff", "path", "type"}),
}
_ACCOUNT_NEUTRAL_REASONING_CONFIG_FIELDS = frozenset({"context", "effort", "summary"})
_ACCOUNT_NEUTRAL_CLIENT_METADATA_FIELDS = frozenset(
    {
        "ws_request_header_x_openai_internal_codex_responses_lite",
        "root_turn_id",
        "session_id",
        "thread_id",
        "turn_id",
        "x-codex-installation-id",
        "x-codex-parent-thread-id",
        "x-codex-turn-metadata",
        "x-codex-window-id",
        "x-openai-subagent",
    }
)
# Codex deliberately omits the potentially large tool namespace inventory
# from the compatibility header while retaining it in the request body. Keep
# the header within conventional proxy limits, but permit a bounded body
# carrier that has already passed the request-size gate and the closed schema
# validation below.
_ACCOUNT_NEUTRAL_TURN_METADATA_DIRECT_MAX_BYTES = 16 * 1024
_ACCOUNT_NEUTRAL_TURN_METADATA_BODY_MAX_BYTES = 1024 * 1024
_ACCOUNT_NEUTRAL_WORKSPACE_KIND_MAX_BYTES = 128
_ACCOUNT_NEUTRAL_TURN_METADATA_FIELDS = frozenset(
    {
        "agent_name",
        "auto_review_enabled",
        "forked_from_thread_id",
        "installation_id",
        "node_repl_auto_review_required",
        "node_repl_disabled",
        "request_kind",
        "root_turn_id",
        "sandbox",
        "sandbox_mode",
        "session_id",
        "thread_id",
        "thread_source",
        "tool_namespaces_info",
        "turn_id",
        "turn_started_at_unix_ms",
        "window_id",
        "workspace_kind",
        "workspaces",
    }
)
_ACCOUNT_NEUTRAL_TURN_METADATA_LINEAGE_FIELDS = frozenset({"parent_thread_id", "parent_turn_id", "subagent_kind"})
_ACCOUNT_SCOPED_HOSTED_INPUT_TYPES = frozenset(
    {
        "code_interpreter_call",
        "computer_call",
        "computer_call_output",
        "file_search_call",
        "image_generation_call",
        "item_reference",
    }
)
_RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION = frozenset(
    {
        "conversation",
        "client_metadata",
        "include",
        "input",
        "instructions",
        "metadata",
        "model",
        "parallel_tool_calls",
        "previous_response_id",
        "prompt",
        "prompt_cache_key",
        "reasoning",
        "service_tier",
        "store",
        "stream",
        "text",
        "tool_choice",
        "tools",
        "truncation",
    }
)


@dataclass(frozen=True, slots=True)
class AccountNeutralReplayProjection:
    input_items: list[JsonValue]
    stored_prefix_count: int
    canonical_lite_developer_index: int | None = None
    """Projected index of the canonical Responses-Lite developer instruction.

    Set only when the original stored prefix begins with a valid
    ``additional_tools`` bundle and the developer message is that bundle's
    original immediate successor. ``None`` keeps every stored developer
    position fail-closed, so projection can never make a non-adjacent
    developer message look canonical.
    """


@dataclass(frozen=True, slots=True)
class AccountNeutralCodexTurnMetadataEvidence:
    session_identity: str
    task_identity: str
    turn_identity: str
    root_turn_identity: str | None
    installation_identity: str | None
    window_identity: str | None
    shared_projection_fingerprint: str


@dataclass(frozen=True, slots=True)
class DirectCallLedgerSummary:
    digest: str
    unresolved_count: int


def responses_direct_call_ledger_summary(
    input_items: list[JsonValue],
) -> DirectCallLedgerSummary | None:
    """Hash the ordered direct-call lifecycle without retaining call content.

    The digest includes only call/output identity, type, and status.  Invalid,
    duplicate, orphaned, or type-mismatched entries are not a settlement
    ledger and fail closed.
    """

    pending: dict[str, str] = {}
    seen: set[str] = set()
    ledger: list[dict[str, str | None]] = []
    for item in input_items:
        if not isinstance(item, dict):
            continue
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if item_type not in _TOOL_CALL_TYPES and item_type not in _TOOL_CALL_TYPE_BY_OUTPUT_TYPE:
            continue
        call_id = item.get("call_id")
        status_value = item.get("status")
        status = status_value if isinstance(status_value, str) else None
        if not isinstance(call_id, str) or not call_id or status not in (None, "completed", "failed"):
            return None
        if item_type in _TOOL_CALL_TYPES:
            if call_id in seen:
                return None
            seen.add(call_id)
            pending[call_id] = item_type
            ledger.append({"call_id": call_id, "kind": "call", "status": status, "type": item_type})
            continue
        expected_call_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE[item_type]
        if pending.get(call_id) != expected_call_type:
            return None
        pending.pop(call_id)
        ledger.append({"call_id": call_id, "kind": "output", "status": status, "type": item_type})
    canonical = json.dumps(ledger, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return DirectCallLedgerSummary(
        digest=sha256(canonical.encode("utf-8")).hexdigest(),
        unresolved_count=len(pending),
    )


def project_responses_input_for_account_neutral_fresh_replay(
    input_items: list[JsonValue],
    *,
    stored_count: int,
    preserve_developer_message_ids: bool = False,
    preserve_response_owned_agent_message_ids: bool = False,
    omit_response_owned_agent_messages_from_stored_prefix: bool = False,
    project_response_owned_developer_messages_from_stored_prefix: bool = False,
    project_response_owned_developer_messages_from_suffix: bool = False,
) -> AccountNeutralReplayProjection | None:
    """Remove known response-owned bookkeeping after durable prefix proof.

    The two ``preserve_*_ids`` options are classification-only evidence for
    response-owned items. A projection created with either option must not be
    serialized as an account-neutral replay payload. The stored-prefix
    developer option is reserved for the separately fingerprint-bound
    abandoned-pending recovery path; it strips response ownership without
    changing developer content or admitting a new developer item.
    """

    if stored_count <= 0 or stored_count > len(input_items):
        return None

    projected_items: list[JsonValue] = []
    projected_stored_count = 0
    canonical_lite_developer_index: int | None = None
    prefix_begins_with_lite_tool_bundle = stored_count >= 2 and _is_canonical_lite_tool_bundle(input_items[0])
    for index, item in enumerate(input_items):
        if (
            omit_response_owned_agent_messages_from_stored_prefix
            and index < stored_count
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and _is_retained_agent_message(item)
        ):
            projected_item = None
        elif (
            project_response_owned_developer_messages_from_stored_prefix
            and index < stored_count
            and isinstance(item, dict)
            and _is_response_owned_developer_message(item)
        ):
            projected_item = dict(item)
            projected_item.pop("id")
            metadata = projected_item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
            if isinstance(metadata, dict):
                projected_item[_INTERNAL_CHAT_MESSAGE_METADATA_FIELD] = {"turn_id": metadata["turn_id"]}
        elif (
            project_response_owned_developer_messages_from_suffix
            and index >= stored_count
            and isinstance(item, dict)
            and _is_response_owned_developer_message(item)
        ):
            projected_item = dict(item)
            projected_item.pop("id")
            metadata = projected_item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
            if isinstance(metadata, dict):
                projected_item[_INTERNAL_CHAT_MESSAGE_METADATA_FIELD] = {"turn_id": metadata["turn_id"]}
        else:
            projected_item = _project_account_neutral_replay_item(
                item,
                preserve_developer_message_ids=preserve_developer_message_ids,
                preserve_response_owned_agent_message_ids=(
                    preserve_response_owned_agent_message_ids
                    and (not omit_response_owned_agent_messages_from_stored_prefix or index >= stored_count)
                ),
            )
        if projected_item is not None:
            projected_items.append(projected_item)
            # The canonical position is the bundle's original immediate
            # successor. Anchoring on the original index keeps a projected-out
            # item between the bundle and a later developer message from
            # collapsing into the same slot.
            if (
                index == 1
                and prefix_begins_with_lite_tool_bundle
                and len(projected_items) == 2
                and _is_inline_developer_message(item)
            ):
                canonical_lite_developer_index = 1
        if index + 1 == stored_count:
            projected_stored_count = len(projected_items)

    return AccountNeutralReplayProjection(
        input_items=projected_items,
        stored_prefix_count=projected_stored_count,
        canonical_lite_developer_index=canonical_lite_developer_index,
    )


def project_responses_input_for_abandoned_pending_fresh_replay(
    input_items: list[JsonValue],
    *,
    stored_count: int,
    pending_tool_calls: Mapping[str, str],
) -> AccountNeutralReplayProjection | None:
    """Project one exact stale-anchor recovery after an abandoned agent call.

    Codex may compact a retained input window so that it begins with a tool
    output whose matching response-owned call is outside the retained window.
    Such an orphan is valid only while the old ``previous_response_id`` still
    supplies that call; forwarding it on an unanchored recovery request is
    invalid. For the narrowly sealed abandoned-pending recovery path, omit a
    leading run of those clipped outputs only when all of the following are
    physically proven by the caller's exact durable-prefix binding:

    * every omitted item is a canonical, account-neutral tool output;
    * none names the abandoned pending call or reuses an id later in context;
    * a later retained assistant output closes over the clipped history; and
    * the remaining stored prefix has a complete direct call/output manifest.

    No call is synthesized or executed. Non-leading or otherwise ambiguous
    orphan outputs remain fail closed.
    """

    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=stored_count,
        preserve_developer_message_ids=True,
        preserve_response_owned_agent_message_ids=True,
        omit_response_owned_agent_messages_from_stored_prefix=True,
        project_response_owned_developer_messages_from_stored_prefix=True,
        project_response_owned_developer_messages_from_suffix=True,
    )
    if projection is None:
        return None

    prefix = projection.input_items[: projection.stored_prefix_count]
    leading_output_count = 0
    leading_call_ids: set[str] = set()
    for item in prefix:
        if not isinstance(item, dict):
            break
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        call_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_type is None:
            break
        call_id = item.get("call_id")
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id in leading_call_ids
            or call_id in pending_tool_calls
            or item.get("status") not in (None, "completed", "failed")
            or not _internal_chat_message_metadata_is_account_neutral(item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD))
            or not _input_item_has_only_known_fields(item, item_type)
            or not _caller_is_self_contained(item)
            or not _tool_output_is_self_contained(item_type or "", item)
        ):
            return None
        leading_call_ids.add(call_id)
        leading_output_count += 1

    if leading_output_count == 0:
        return projection

    retained_prefix = prefix[leading_output_count:]
    if not retained_prefix or not any(
        isinstance(item, dict) and _is_retained_response_message(item) for item in retained_prefix
    ):
        return None
    if any(
        isinstance(item, dict) and item.get("call_id") in leading_call_ids
        for item in projection.input_items[leading_output_count:]
    ):
        return None

    projected_canonical_index = (
        None
        if projection.canonical_lite_developer_index is None
        else projection.canonical_lite_developer_index - leading_output_count
    )
    if (
        _direct_tool_call_prefix_state(
            retained_prefix,
            allow_exact_stored_developer_items=True,
            canonical_lite_developer_index=projected_canonical_index,
        )
        is None
    ):
        return None

    return AccountNeutralReplayProjection(
        input_items=[*retained_prefix, *projection.input_items[projection.stored_prefix_count :]],
        stored_prefix_count=len(retained_prefix),
        canonical_lite_developer_index=projected_canonical_index,
    )


def _is_canonical_lite_tool_bundle(item: JsonValue) -> bool:
    return (
        isinstance(item, dict)
        and item.get("type") == "additional_tools"
        and item.get("role") == "developer"
        and _input_item_has_only_known_fields(item, "additional_tools")
        and _tools_are_account_neutral(item.get("tools"))
    )


def _is_inline_developer_message(item: JsonValue) -> bool:
    return isinstance(item, dict) and item.get("role") == "developer" and item.get("type") != "additional_tools"


def _project_account_neutral_replay_item(
    item: JsonValue,
    *,
    preserve_developer_message_ids: bool,
    preserve_response_owned_agent_message_ids: bool,
) -> JsonValue | None:
    if not isinstance(item, dict):
        return item

    item_type = item.get("type")
    if preserve_developer_message_ids and item.get("role") == "developer" and item_type != "additional_tools":
        # Classification must see every inline developer-role item before
        # projection can omit response-owned bookkeeping types. The
        # additional_tools bundle is a distinct Responses-Lite input item,
        # not an inline developer message.
        return item
    if preserve_response_owned_agent_message_ids and item_type == "agent_message":
        return item
    if _is_response_owned_user_message(item):
        projected_item = dict(item)
        projected_item.pop("id")
        metadata = projected_item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
        if isinstance(metadata, dict):
            projected_item[_INTERNAL_CHAT_MESSAGE_METADATA_FIELD] = {"turn_id": metadata["turn_id"]}
        return projected_item
    if item_type is not None and not isinstance(item_type, str):
        return item
    if item_type == "reasoning" or (
        item_type in _ACCOUNT_NEUTRAL_REPLAY_OMITTED_ITEM_TYPES and item.get("status") == "completed"
    ):
        return None

    if "id" not in item:
        return item
    projected_item = dict(item)
    projected_item.pop("id")
    metadata = projected_item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
    if (
        isinstance(metadata, dict)
        and set(metadata) == _RESPONSE_OWNED_AGENT_MESSAGE_METADATA_FIELDS
        and _is_nonblank_string(metadata.get("turn_id"))
        and _is_finite_nonnegative_number(metadata.get("create_time"))
    ):
        # Codex persists response-owned calls, outputs, and assistant messages
        # with the same creation timestamp bookkeeping as user messages. The
        # timestamp belongs to the old response and is not replay authority;
        # retain only the request-scoped turn id after the response-owned id
        # has been removed.
        projected_item[_INTERNAL_CHAT_MESSAGE_METADATA_FIELD] = {"turn_id": metadata["turn_id"]}
    return projected_item


def responses_input_items_are_self_contained_fresh_replay(input_items: list[JsonValue]) -> bool:
    unsettled_call_ids_by_type: dict[str, set[str]] = {item_type: set() for item_type in _TOOL_CALL_TYPES}
    seen_call_ids: set[str] = set()
    settled_call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict):
            return False
        if "type" in item and not _is_nonblank_string(item.get("type")):
            return False
        if item.get("id") not in (None, ""):
            return False
        if not _internal_chat_message_metadata_is_account_neutral(item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)):
            return False
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if not _input_item_has_only_known_fields(item, item_type):
            return False
        call_id_value = item.get("call_id")
        call_id = call_id_value if isinstance(call_id_value, str) and call_id_value else None
        if item_type in _TOOL_CALL_TYPES:
            if (
                call_id is None
                or call_id in seen_call_ids
                or not _caller_is_self_contained(item)
                or not _tool_call_is_self_contained(item_type, item)
            ):
                return False
            seen_call_ids.add(call_id)
            unsettled_call_ids_by_type[item_type].add(call_id)
            continue
        call_item_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_item_type is not None:
            if (
                call_id is None
                or call_id not in unsettled_call_ids_by_type[call_item_type]
                or call_id in settled_call_ids
                or not _caller_is_self_contained(item)
                or not _tool_output_is_self_contained(item_type or "", item)
            ):
                return False
            unsettled_call_ids_by_type[call_item_type].remove(call_id)
            settled_call_ids.add(call_id)
    return all(not call_ids for call_ids in unsettled_call_ids_by_type.values())


def responses_input_items_are_self_contained_rowless_replay(
    original_items: list[JsonValue],
    projected_items: list[JsonValue],
) -> bool:
    """Admit only canonical, settled Codex agent deliveries for rowless replay."""

    pending_calls: set[str] = set()
    agent_indexes: list[int] = []
    for index, item in enumerate(original_items):
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        call_id = item.get("call_id")
        if item_type in _TOOL_CALL_TYPES and isinstance(call_id, str):
            pending_calls.add(call_id)
        elif item_type in _TOOL_CALL_TYPE_BY_OUTPUT_TYPE and isinstance(call_id, str):
            pending_calls.discard(call_id)
        if item_type != "agent_message":
            continue
        normalized_agent = _normalized_rowless_agent_message(item)
        if pending_calls or normalized_agent is None or not _is_retained_agent_message(normalized_agent):
            return False
        agent_indexes.append(index)
        if not any(isinstance(later, dict) and later.get("role") == "user" for later in original_items[index + 1 :]):
            return False
    if not agent_indexes:
        return responses_input_items_are_self_contained_fresh_replay(projected_items)

    projected_without_agents: list[JsonValue] = []
    for item in projected_items:
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            projected_without_agents.append(item)
            continue
        metadata = item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
        content = item.get("content")
        if (
            set(item)
            not in {
                frozenset({"author", "content", "recipient", "type"}),
                frozenset(
                    {
                        "author",
                        "content",
                        _INTERNAL_CHAT_MESSAGE_METADATA_FIELD,
                        "recipient",
                        "type",
                    }
                ),
            }
            or not isinstance(item.get("author"), str)
            or not _AGENT_PATH_PATTERN.fullmatch(cast(str, item["author"]))
            or not isinstance(item.get("recipient"), str)
            or not _AGENT_PATH_PATTERN.fullmatch(cast(str, item["recipient"]))
            or item["author"] == item["recipient"]
            or not _internal_chat_message_metadata_is_account_neutral(metadata)
            or not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "input_text"
            or not _input_content_part_is_self_contained(
                cast(dict[str, JsonValue], content[0]),
                allow_output=False,
            )
        ):
            return False
    return responses_input_items_are_self_contained_fresh_replay(projected_without_agents)


def normalize_responses_input_for_rowless_replay(
    projected_items: list[JsonValue],
) -> list[JsonValue] | None:
    """Drop only canonical, semantics-free response transport artifacts.

    Codex can persist an empty ``input_text`` tail in a direct tool output and
    an opaque ``encrypted_content`` sibling beside the delivered text of an
    inter-agent message.  Neither item carries replayable conversation
    semantics.  Keep the original request fingerprint unchanged, but remove
    those exact shapes from the separately fingerprinted rowless projection.
    Any drift in fields, ordering, multiplicity, or non-empty text remains
    fail closed.
    """

    normalized_items: list[JsonValue] = []
    for item in projected_items:
        if not isinstance(item, dict):
            normalized_items.append(item)
            continue

        if item.get("type") == "agent_message":
            normalized = _normalized_rowless_agent_message(item)
            if normalized is None:
                return None
            normalized_items.append(normalized)
            continue

        item_type = item.get("type")
        if item_type in _TOOL_CALL_TYPE_BY_OUTPUT_TYPE and isinstance(item.get("output"), list):
            output = cast(list[JsonValue], item["output"])
            filtered_output = [part for part in output if not _is_exact_empty_input_text_part(part)]
            if len(filtered_output) != len(output):
                if not filtered_output:
                    return None
                normalized_item = dict(item)
                normalized_item["output"] = filtered_output
                normalized_items.append(normalized_item)
                continue

        normalized_items.append(item)
    return normalized_items


def _is_exact_empty_input_text_part(part: JsonValue) -> bool:
    return (
        isinstance(part, dict)
        and set(part) == {"text", "type"}
        and part.get("type") == "input_text"
        and part.get("text") == ""
    )


def _normalized_rowless_agent_message(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    content = item.get("content")
    if not isinstance(content, list):
        return None
    input_parts = [
        part
        for part in content
        if isinstance(part, dict)
        and part.get("type") == "input_text"
        and _input_content_part_is_self_contained(part, allow_output=False)
    ]
    encrypted_parts = [
        part
        for part in content
        if isinstance(part, dict)
        and set(part) == {"encrypted_content", "type"}
        and part.get("type") == "encrypted_content"
        and _is_nonblank_string(part.get("encrypted_content"))
    ]
    if len(input_parts) != 1 or len(encrypted_parts) > 1 or len(input_parts) + len(encrypted_parts) != len(content):
        return None
    if encrypted_parts and content != [input_parts[0], encrypted_parts[0]]:
        return None
    normalized = dict(item)
    normalized["content"] = [input_parts[0]]
    return normalized


def _internal_chat_message_metadata_is_account_neutral(value: JsonValue | None) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == _ACCOUNT_NEUTRAL_INTERNAL_CHAT_MESSAGE_METADATA_FIELDS
        and _is_nonblank_string(value.get("turn_id"))
    )


def responses_input_suffix_retains_prior_output(
    input_items: list[JsonValue],
    *,
    stored_count: int,
    canonical_lite_developer_index: int | None = None,
    exact_stored_prefix_without_pending_manifest: bool = False,
    allow_response_owned_agent_message: bool = True,
) -> bool:
    """Prove that a stored input prefix is followed by prior output and new input."""

    if stored_count <= 0 or len(input_items) <= stored_count:
        return False
    stored_prefix = input_items[:stored_count]
    if exact_stored_prefix_without_pending_manifest:
        # A store-context proof binds this prefix byte-for-byte to the input
        # already completed by the same live/durable session and separately
        # identifies the exact historical input boundary. It therefore need
        # not reinterpret valid account-neutral developer items inside that
        # sealed prefix as new cross-account authority. Still parse every
        # direct call/output pair so an output crossing the stored boundary is
        # retained and appended call-id reuse remains fail-closed.
        prefix_state = _direct_tool_call_prefix_state(
            stored_prefix,
            allow_exact_stored_developer_items=True,
            canonical_lite_developer_index=canonical_lite_developer_index,
        )
    else:
        prefix_state = _direct_tool_call_prefix_state(
            stored_prefix,
            canonical_lite_developer_index=canonical_lite_developer_index,
        )
    if prefix_state is None:
        return False
    pending_suffix_calls, seen_suffix_call_ids = prefix_state
    retained_output_seen = False
    retained_output_is_final_answer = False
    fresh_followup_seen = False
    fresh_followup_count = 0
    fresh_followup_is_user_message = False
    fresh_developer_followup_seen = False
    for item in input_items[stored_count:]:
        if fresh_developer_followup_seen or not isinstance(item, dict):
            return False
        item_type_value = item.get("type")
        if "type" in item and not _is_nonblank_string(item_type_value):
            return False
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if item_type in _TOOL_CALL_TYPES:
            if item.get("status") not in (None, "completed"):
                return False
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in seen_suffix_call_ids:
                return False
            seen_suffix_call_ids.add(call_id)
            pending_suffix_calls.append((item_type, call_id))
            # Without a persisted output manifest, a call/output pair cannot
            # prove that an omitted parallel call was not part of the response.
            # Require a later completed assistant message as the turn boundary.
            retained_output_seen = False
            retained_output_is_final_answer = False
            fresh_followup_seen = False
            fresh_followup_count = 0
            fresh_followup_is_user_message = False
            continue
        call_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_type is not None:
            if item.get("status") not in (None, "completed", "failed"):
                return False
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not pending_suffix_calls:
                return False
            if pending_suffix_calls[0] != (call_type, call_id):
                return False
            pending_suffix_calls.popleft()
            continue
        if item_type in (None, "message") and item.get("role") == "assistant":
            if pending_suffix_calls or not _is_retained_response_message(item):
                return False
            retained_output_seen = True
            retained_output_is_final_answer = item.get("phase") == "final_answer"
            fresh_followup_seen = False
            fresh_followup_count = 0
            fresh_followup_is_user_message = False
            continue
        if item_type == "agent_message":
            if (
                not allow_response_owned_agent_message
                or pending_suffix_calls
                or retained_output_seen
                or fresh_followup_seen
                or not _is_retained_agent_message(item)
            ):
                return False
            retained_output_seen = True
            # An inter-agent delivery closes the prior task but is not an
            # assistant final answer. Keep the stricter developer-followup
            # rule while still permitting one or more later user messages.
            retained_output_is_final_answer = False
            continue
        if _is_fresh_followup_input(item):
            if not retained_output_seen or pending_suffix_calls:
                return False
            fresh_followup_seen = True
            fresh_followup_count += 1
            fresh_followup_is_user_message = item_type in (None, "message") and item.get("role") == "user"
            continue
        if _fresh_developer_message_is_transparent(item):
            if (
                not fresh_followup_seen
                or fresh_followup_count != 1
                or not fresh_followup_is_user_message
                or not retained_output_is_final_answer
                or pending_suffix_calls
            ):
                return False
            fresh_developer_followup_seen = True
            continue
        return False
    return retained_output_seen and fresh_followup_seen and not pending_suffix_calls


def responses_input_suffix_matches_pending_tool_calls(
    input_items: list[JsonValue],
    *,
    stored_count: int,
    pending_tool_calls: Mapping[str, str],
    canonical_lite_developer_index: int | None = None,
) -> bool:
    """Prove the suffix exactly settles the durable prior-response call manifest.

    A completed call/output manifest is the physical client-side settlement
    proof.  Codex can retain bounded later user/inter-agent inputs after that
    settlement in the same complete-context resend.  Those later inputs do
    not weaken the proof, but another tool loop does: the latter could belong
    to a different response and must never stand in for the durable manifest.
    """

    if stored_count <= 0 or len(input_items) <= stored_count or not pending_tool_calls:
        return False
    prefix_state = _direct_tool_call_prefix_state(
        input_items[:stored_count],
        allow_historical_developer_interleave=True,
        canonical_lite_developer_index=canonical_lite_developer_index,
    )
    if prefix_state is None or prefix_state[0] or prefix_state[1] & pending_tool_calls.keys():
        return False
    suffix = input_items[stored_count:]
    settlement_end = _exact_pending_tool_call_settlement_prefix_length(
        suffix,
        pending_tool_calls=pending_tool_calls,
    )
    if settlement_end is None:
        return False
    followups = suffix[settlement_end:]
    if not followups:
        return True
    if any(isinstance(item, dict) and item.get("role") == "developer" for item in suffix[:settlement_end]):
        # The historical one-call developer interleave exception is sealed to
        # that exact three-item window. It is not authority for accepting a
        # later turn boundary or user follow-up.
        return False
    first = followups[0]
    if isinstance(first, dict) and first.get("type") == "agent_message":
        if not _is_retained_agent_message(first):
            return False
        followups = followups[1:]
    return _abandoned_pending_followup_sequence_is_bounded(
        followups,
        allow_response_owned_messages=False,
    )


def _exact_pending_tool_call_settlement_prefix_length(
    input_items: list[JsonValue],
    *,
    pending_tool_calls: Mapping[str, str],
) -> int | None:
    """Return the shortest prefix that exactly settles one durable manifest."""

    expected = dict(pending_tool_calls)
    for end in range(1, len(input_items) + 1):
        candidate = input_items[:end]
        normalized = candidate
        if (
            len(candidate) == 3
            and isinstance(candidate[1], dict)
            and _fresh_developer_message_is_transparent(candidate[1])
            and _fresh_developer_interleave_is_bounded(candidate, index=1)
        ):
            normalized = [candidate[0], candidate[2]]
        if not all(
            isinstance(item, dict)
            and isinstance(item.get("type"), str)
            and item.get("type") in (_TOOL_CALL_TYPES | _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.keys())
            for item in normalized
        ):
            # A bounded call/developer/output window becomes recognizable
            # only when its output arrives. Keep scanning possible prefixes;
            # an unrelated non-settlement item will remain in every later
            # candidate and therefore can never satisfy this predicate.
            continue
        if not responses_input_items_are_self_contained_fresh_replay(normalized):
            continue
        suffix_calls: dict[str, str] = {}
        suffix_outputs: dict[str, str] = {}
        for item in cast(list[dict[str, JsonValue]], normalized):
            item_type = cast(str, item["type"])
            call_id = cast(str, item["call_id"])
            if item_type in _TOOL_CALL_TYPES:
                suffix_calls[call_id] = item_type
            else:
                suffix_outputs[call_id] = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE[item_type]
        if suffix_calls == expected and suffix_outputs == expected:
            return end
    return None


def responses_input_suffix_proves_abandoned_pending_agent_boundary(
    input_items: list[JsonValue],
    *,
    stored_count: int,
    pending_tool_calls: Mapping[str, str],
) -> bool:
    """Prove a later inter-agent boundary excludes an undelivered pending call.

    This is deliberately narrower than ordinary fresh-replay eligibility.  It
    exists for one stale-anchor recovery case: the durable response manifest
    records a pending client-side tool call, the exact client resend contains
    none of that call's ids, and a canonical response-owned ``agent_message``
    followed by fresh user input proves that the client advanced without ever
    accepting or executing the pending call.  The caller must additionally
    prove that upstream rejected the exact response anchor before emitting any
    response event; this predicate alone never authorizes proactive replay.
    """

    return (
        abandoned_pending_agent_boundary_rejection_reason(
            input_items,
            stored_count=stored_count,
            pending_tool_calls=pending_tool_calls,
        )
        is None
    )


def abandoned_pending_agent_boundary_rejection_reason(
    input_items: list[JsonValue],
    *,
    stored_count: int,
    pending_tool_calls: Mapping[str, str],
) -> AbandonedPendingBoundaryRejectionReason | None:
    """Return the first content-free failure branch for boundary proof."""

    if stored_count <= 0 or len(input_items) <= stored_count:
        return "stored_prefix_invalid"
    if not pending_tool_calls:
        return "pending_call_manifest_missing"
    raw_suffix = input_items[stored_count:]
    boundary_index = 0
    while boundary_index < len(raw_suffix) and isinstance(raw_suffix[boundary_index], dict):
        item = cast(dict[str, JsonValue], raw_suffix[boundary_index])
        if item.get("type") != "reasoning":
            break
        if not _is_response_owned_reasoning_boundary_item(item):
            return "boundary_reasoning_shape_invalid"
        boundary_index += 1
    if boundary_index >= len(raw_suffix):
        return "boundary_agent_message_shape_invalid"
    boundary = raw_suffix[boundary_index]
    if not isinstance(boundary, dict) or not _is_retained_agent_message(boundary):
        return "boundary_agent_message_shape_invalid"
    followups = raw_suffix[boundary_index + 1 :]
    followup_rejection = _abandoned_pending_followup_sequence_rejection_reason(
        followups,
        allow_response_owned_messages=True,
    )
    if followup_rejection is not None:
        return followup_rejection
    for item in input_items:
        if isinstance(item, dict) and item.get("call_id") in pending_tool_calls:
            return "pending_call_conflict"
    replay_projection = project_responses_input_for_abandoned_pending_fresh_replay(
        input_items,
        stored_count=stored_count,
        pending_tool_calls=pending_tool_calls,
    )
    if replay_projection is None:
        return "projection_failed"
    prefix_state = _direct_tool_call_prefix_state(
        replay_projection.input_items[: replay_projection.stored_prefix_count],
        allow_exact_stored_developer_items=True,
        canonical_lite_developer_index=replay_projection.canonical_lite_developer_index,
    )
    if prefix_state is None or prefix_state[0]:
        return "direct_call_prefix_state_invalid"
    if prefix_state[1] & pending_tool_calls.keys():
        return "pending_call_conflict"
    suffix = replay_projection.input_items[replay_projection.stored_prefix_count :]
    if len(suffix) < 2:
        return "followup_missing"
    first = suffix[0]
    if not isinstance(first, dict) or first.get("type") != "agent_message" or not _is_retained_agent_message(first):
        return "projected_boundary_invalid"
    if not _abandoned_pending_followup_sequence_is_bounded(
        suffix[1:],
        allow_response_owned_messages=False,
    ):
        return "projected_followup_invalid"
    return None


def _abandoned_pending_followup_sequence_is_bounded(
    input_items: list[JsonValue],
    *,
    allow_response_owned_messages: bool,
) -> bool:
    """Require one bounded developer refresh between proven user followups."""

    return (
        _abandoned_pending_followup_sequence_rejection_reason(
            input_items,
            allow_response_owned_messages=allow_response_owned_messages,
        )
        is None
    )


def _abandoned_pending_followup_sequence_rejection_reason(
    input_items: list[JsonValue],
    *,
    allow_response_owned_messages: bool,
) -> AbandonedPendingBoundaryRejectionReason | None:
    """Classify one bounded follow-up sequence without exposing content."""

    if not input_items:
        return "followup_missing"
    user_seen = False
    developer_seen = False
    user_after_developer_seen = False
    for item in input_items:
        if not isinstance(item, dict):
            return "followup_shape_invalid"
        is_user = _is_fresh_followup_input(item) or (
            allow_response_owned_messages and _is_response_owned_user_message(item)
        )
        item_type_value = item.get("type")
        item_type = item_type_value if isinstance(item_type_value, str) else None
        is_developer = (
            _is_response_owned_developer_message(item)
            if allow_response_owned_messages
            else _historical_pending_developer_message_is_transparent(item, item_type=item_type)
        )
        if is_user:
            user_seen = True
            if developer_seen:
                user_after_developer_seen = True
            continue
        if is_developer:
            if developer_seen or not user_seen:
                return "developer_message_sequence_invalid"
            developer_seen = True
            continue
        if item.get("role") == "developer":
            return "developer_message_shape_invalid"
        return "followup_shape_invalid"
    if not user_seen:
        return "followup_missing"
    if developer_seen and not user_after_developer_seen:
        return "developer_message_sequence_invalid"
    return None


def _is_response_owned_reasoning_boundary_item(item: Mapping[str, JsonValue]) -> bool:
    """Recognize the exact Codex response bookkeeping allowed before a boundary."""

    allowed_fields = {
        "content",
        "encrypted_content",
        "id",
        _INTERNAL_CHAT_MESSAGE_METADATA_FIELD,
        "status",
        "summary",
        "type",
    }
    metadata = item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
    summary = item.get("summary")
    status = item.get("status")
    return (
        set(item) <= allowed_fields
        and {"encrypted_content", "id", "summary", "type"} <= set(item)
        and item.get("type") == "reasoning"
        and isinstance(item.get("id"), str)
        and cast(str, item["id"]).startswith("rs_")
        and _is_nonblank_string(item.get("encrypted_content"))
        and isinstance(summary, list)
        and all(
            isinstance(part, dict)
            and set(part) == {"text", "type"}
            and part.get("type") == "summary_text"
            and isinstance(part.get("text"), str)
            for part in summary
        )
        and (
            (
                metadata is None
                # Codex sends ``content: null`` on the HTTP transport. The
                # ResponsesRequest model intentionally drops that null field
                # before the recovery predicate sees the item, so both exact
                # representations describe the same response-owned boundary.
                # No non-null content or additional field is admitted.
                and ("content" not in item or item.get("content") is None)
            )
            or (
                isinstance(metadata, dict)
                and "content" not in item
                and set(metadata) == _ACCOUNT_NEUTRAL_INTERNAL_CHAT_MESSAGE_METADATA_FIELDS
                and _is_uuid(metadata.get("turn_id"))
            )
        )
        and status in (None, "completed")
    )


def _direct_tool_call_prefix_state(
    input_items: list[JsonValue],
    *,
    allow_historical_developer_interleave: bool = False,
    allow_exact_stored_developer_items: bool = False,
    canonical_lite_developer_index: int | None = None,
) -> tuple[deque[tuple[str, str]], set[str]] | None:
    pending_calls: deque[tuple[str, str]] = deque()
    seen_call_ids: set[str] = set()
    # A pending window opens when ``pending_calls`` becomes non-empty and closes when it
    # drains. Historical interleaving is proven only for a window that never held more than
    # one outstanding call and never consumed more than one developer message.
    pending_window_developer_seen = False
    pending_window_held_parallel_calls = False
    for index, item in enumerate(input_items):
        if not isinstance(item, dict):
            return None
        item_type_value = item.get("type")
        if item_type_value is not None and not isinstance(item_type_value, str):
            return None
        item_type = item_type_value if isinstance(item_type_value, str) else None
        if item.get("role") == "developer" and item_type != "additional_tools":
            developer_message_is_transparent = _historical_pending_developer_message_is_transparent(
                item,
                item_type=item_type,
            )
            # Canonical position is proven by the projection against the
            # original input, not by adjacency inside the projected prefix.
            occupies_canonical_lite_position = (
                canonical_lite_developer_index is not None and index == canonical_lite_developer_index
            )
            if developer_message_is_transparent and occupies_canonical_lite_position:
                continue
            if developer_message_is_transparent and allow_exact_stored_developer_items:
                continue
            historical_interleave_is_bounded = (
                allow_historical_developer_interleave
                and len(pending_calls) == 1
                and not pending_window_held_parallel_calls
                and not pending_window_developer_seen
            )
            if developer_message_is_transparent and historical_interleave_is_bounded:
                pending_window_developer_seen = True
                continue
            return None
        if item_type in _TOOL_CALL_TYPES:
            if item.get("status") not in (None, "completed"):
                return None
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in seen_call_ids:
                return None
            seen_call_ids.add(call_id)
            pending_calls.append((item_type, call_id))
            if len(pending_calls) > 1:
                # A window that already spent its interleaved developer message must not
                # become parallel afterwards, otherwise a batch is admitted by ordering the
                # developer message before the second call.
                if pending_window_developer_seen:
                    return None
                pending_window_held_parallel_calls = True
            continue
        call_type = _TOOL_CALL_TYPE_BY_OUTPUT_TYPE.get(item_type or "")
        if call_type is not None:
            if item.get("status") not in (None, "completed", "failed"):
                return None
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not pending_calls:
                return None
            if pending_calls[0] != (call_type, call_id):
                return None
            pending_calls.popleft()
            if not pending_calls:
                pending_window_developer_seen = False
                pending_window_held_parallel_calls = False
            continue
        if pending_calls and (
            (item_type in (None, "message") and item.get("role") in _ACCOUNT_NEUTRAL_MESSAGE_ROLES)
            or item_type in {"input_file", "input_image", "input_text"}
        ):
            return None
        # Call-like items outside the supported direct tool-call vocabulary
        # (e.g. computer_call) are tolerated in the prefix but must still
        # surface their IDs for collision checks: a pending call that reuses
        # one of them cannot be proven fresh.
        fallthrough_call_id = item.get("call_id")
        if isinstance(fallthrough_call_id, str) and fallthrough_call_id:
            seen_call_ids.add(fallthrough_call_id)
    return pending_calls, seen_call_ids


def _historical_pending_developer_message_is_transparent(
    item: Mapping[str, JsonValue],
    *,
    item_type: str | None,
) -> bool:
    return (
        item_type in (None, "message")
        and ("type" not in item or _is_nonblank_string(item.get("type")))
        and item.get("role") == "developer"
        and item.get("id") is None
        and item.get("phase") is None
        and item.get("status") in (None, "completed")
        and _internal_chat_message_metadata_is_account_neutral(item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD))
        and _input_item_has_only_known_fields(item, item_type)
        and _message_has_valid_account_neutral_content(item)
    )


def _fresh_developer_interleave_is_bounded(
    input_items: list[JsonValue],
    *,
    index: int,
) -> bool:
    if len(input_items) != 3 or index != 1:
        return False
    preceding_item = input_items[0]
    following_item = input_items[2]
    if not isinstance(preceding_item, dict) or not isinstance(following_item, dict):
        return False
    call_type = preceding_item.get("type")
    output_type = following_item.get("type")
    call_id = preceding_item.get("call_id")
    return (
        call_type == "custom_tool_call"
        and output_type == "custom_tool_call_output"
        and _is_nonblank_string(call_id)
        and following_item.get("call_id") == call_id
    )


def _fresh_developer_message_is_transparent(
    item: Mapping[str, JsonValue],
) -> bool:
    item_type_value = item.get("type")
    item_type = item_type_value if isinstance(item_type_value, str) else None
    metadata = item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
    content = item.get("content")
    return (
        ("type" not in item or _is_nonblank_string(item.get("type")))
        and item_type in (None, "message")
        and item.get("role") == "developer"
        and item.get("id") in (None, "")
        and item.get("phase") is None
        and item.get("status") in (None, "completed")
        and isinstance(metadata, dict)
        and _internal_chat_message_metadata_is_account_neutral(metadata)
        and _input_item_has_only_known_fields(item, item_type)
        and isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and content[0].get("type") == "input_text"
        and _input_content_part_is_self_contained(
            cast(dict[str, JsonValue], content[0]),
            allow_output=False,
        )
    )


def _is_retained_response_message(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    if (
        item_type not in (None, "message")
        or item.get("role") != "assistant"
        or item.get("status") not in (None, "completed")
    ):
        return False
    return _message_has_valid_account_neutral_content(item)


def _is_retained_agent_message(item: Mapping[str, JsonValue]) -> bool:
    """Validate the exact response-owned Codex inter-agent delivery shape."""

    item_id = item.get("id")
    author = item.get("author")
    recipient = item.get("recipient")
    metadata = item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
    content = item.get("content")
    if (
        set(item)
        not in {
            _RESPONSE_OWNED_AGENT_MESSAGE_FIELDS,
            _TRANSPORT_RESPONSE_OWNED_AGENT_MESSAGE_FIELDS,
        }
        or item.get("type") != "agent_message"
        or not isinstance(item_id, str)
        or not item_id.startswith("amsg_")
        or not _is_uuid(item_id.removeprefix("amsg_"))
        or not isinstance(author, str)
        or not _AGENT_PATH_PATTERN.fullmatch(author)
        or not isinstance(recipient, str)
        or not _AGENT_PATH_PATTERN.fullmatch(recipient)
        or author == recipient
        or not (
            metadata is None
            or (
                isinstance(metadata, dict)
                and set(metadata) == _RESPONSE_OWNED_AGENT_MESSAGE_METADATA_FIELDS
                and _is_uuid(metadata.get("turn_id"))
                and _is_finite_nonnegative_number(metadata.get("create_time"))
            )
        )
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "input_text"
    ):
        return False
    return _input_content_part_is_self_contained(
        cast(dict[str, JsonValue], content[0]),
        allow_output=False,
    )


def _is_response_owned_user_message(item: Mapping[str, JsonValue]) -> bool:
    """Validate Codex's persisted user-message bookkeeping before stripping it."""

    item_id = item.get("id")
    metadata = item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
    content = item.get("content")
    if (
        set(item)
        not in {
            _RESPONSE_OWNED_USER_MESSAGE_FIELDS,
            _TRANSPORT_RESPONSE_OWNED_USER_MESSAGE_FIELDS,
        }
        or item.get("type") != "message"
        or item.get("role") != "user"
        or not isinstance(item_id, str)
        or not item_id.startswith("msg_")
        or not _is_uuid(item_id.removeprefix("msg_"))
        or not (
            metadata is None
            or (
                isinstance(metadata, dict)
                and set(metadata) == _RESPONSE_OWNED_AGENT_MESSAGE_METADATA_FIELDS
                and _is_uuid(metadata.get("turn_id"))
                and _is_finite_nonnegative_number(metadata.get("create_time"))
            )
        )
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "input_text"
    ):
        return False
    return _input_content_part_is_self_contained(
        cast(dict[str, JsonValue], content[0]),
        allow_output=False,
    )


def _is_response_owned_developer_message(item: Mapping[str, JsonValue]) -> bool:
    """Validate a persisted developer message inside an exact stored prefix."""

    item_id = item.get("id")
    metadata = item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD)
    content = item.get("content")
    return (
        set(item)
        in {
            _RESPONSE_OWNED_USER_MESSAGE_FIELDS,
            _TRANSPORT_RESPONSE_OWNED_USER_MESSAGE_FIELDS,
        }
        and item.get("type") == "message"
        and item.get("role") == "developer"
        and isinstance(item_id, str)
        and item_id.startswith("msg_")
        and _is_uuid(item_id.removeprefix("msg_"))
        and (
            metadata is None
            or (
                isinstance(metadata, dict)
                and set(metadata) == _ACCOUNT_NEUTRAL_INTERNAL_CHAT_MESSAGE_METADATA_FIELDS
                and _is_uuid(metadata.get("turn_id"))
            )
            or (
                isinstance(metadata, dict)
                and set(metadata) == _RESPONSE_OWNED_AGENT_MESSAGE_METADATA_FIELDS
                and _is_uuid(metadata.get("turn_id"))
                and _is_finite_nonnegative_number(metadata.get("create_time"))
            )
        )
        and isinstance(content, list)
        and bool(content)
        and all(
            isinstance(part, dict)
            and part.get("type") == "input_text"
            and _input_content_part_is_self_contained(part, allow_output=False)
            for part in content
        )
    )


def _is_uuid(value: JsonValue | None) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


def _is_finite_nonnegative_number(value: JsonValue | None) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # Python integers are unbounded, while JSON numbers accepted by the
        # upstream timestamp contract must still be representable as finite
        # numeric metadata.  Oversized integers therefore fail closed.
        return False


def _is_fresh_followup_input(item: Mapping[str, JsonValue]) -> bool:
    item_type = item.get("type")
    if item_type in {"input_file", "input_image", "input_text"}:
        return _input_item_has_only_known_fields(item, cast(str, item_type)) and _input_content_part_is_self_contained(
            item,
            allow_output=False,
        )
    return (
        item_type in (None, "message")
        and item.get("role") == "user"
        and item.get("id") in (None, "")
        and item.get("phase") is None
        and item.get("status") in (None, "completed")
        and _internal_chat_message_metadata_is_account_neutral(item.get(_INTERNAL_CHAT_MESSAGE_METADATA_FIELD))
        and _input_item_has_only_known_fields(item, cast(str | None, item_type))
        and _message_has_valid_account_neutral_content(item)
    )


def _tool_call_is_self_contained(item_type: str, item: Mapping[str, JsonValue]) -> bool:
    if item.get("status") not in (None, "completed"):
        return False
    if item_type == "function_call":
        return (
            _is_nonblank_string(item.get("name"))
            and isinstance(item.get("arguments"), str)
            and (item.get("namespace") is None or _is_nonblank_string(item.get("namespace")))
        )
    if item_type == "custom_tool_call":
        return _is_nonblank_string(item.get("name")) and isinstance(item.get("input"), str)
    operation = item.get("operation")
    patch = item.get("patch")
    input_value = item.get("input")
    if sum(field in item for field in ("operation", "patch", "input")) != 1:
        return False
    if "operation" in item:
        return _apply_patch_operation_is_self_contained(operation)
    if "patch" in item:
        return _is_nonblank_string(patch)
    return _is_nonblank_string(input_value)


def _caller_is_self_contained(item: Mapping[str, JsonValue]) -> bool:
    caller = item.get("caller")
    return caller is None or caller == {"type": "direct"}


def _input_item_has_only_known_fields(item: Mapping[str, JsonValue], item_type: str | None) -> bool:
    if item_type in (None, "message"):
        allowed_fields = _ACCOUNT_NEUTRAL_MESSAGE_FIELDS
    elif item_type in _ACCOUNT_NEUTRAL_CONTENT_FIELDS:
        allowed_fields = _ACCOUNT_NEUTRAL_CONTENT_FIELDS[item_type]
    else:
        allowed_fields = _ACCOUNT_NEUTRAL_INPUT_ITEM_FIELDS.get(item_type or "")
        if allowed_fields is None:
            return False
    status = item.get("status")
    return not any(key not in allowed_fields for key in item) and (
        status is None or (isinstance(status, str) and status in _ACCOUNT_NEUTRAL_ITEM_STATUSES)
    )


def _apply_patch_operation_is_self_contained(operation: JsonValue | None) -> bool:
    if not isinstance(operation, dict):
        return False
    operation_type = operation.get("type")
    allowed_fields = (
        _ACCOUNT_NEUTRAL_APPLY_PATCH_OPERATION_FIELDS.get(operation_type) if isinstance(operation_type, str) else None
    )
    if allowed_fields is None or set(operation) != allowed_fields:
        return False
    return _is_nonblank_string(operation.get("path")) and (
        operation_type == "delete_file" or isinstance(operation.get("diff"), str)
    )


def _tool_output_is_self_contained(item_type: str, item: Mapping[str, JsonValue]) -> bool:
    if item.get("status") not in (None, "completed", "failed"):
        return False
    output = item.get("output")
    if isinstance(output, str):
        return True
    if item_type == "apply_patch_call_output":
        return output is None and item.get("status") in {"completed", "failed"}
    return (
        isinstance(output, list)
        and bool(output)
        and all(
            isinstance(part, dict) and _input_content_part_is_self_contained(part, allow_output=False)
            for part in output
        )
    )


def _is_nonblank_string(value: JsonValue | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def responses_payload_is_account_neutral_fresh_replay(
    payload: Mapping[str, JsonValue],
    *,
    expected_session_identity: str | None = None,
    expected_task_identity: str | None = None,
) -> bool:
    """Return whether a full request can move accounts without stored upstream state."""

    if payload.get("conversation") not in (None, ""):
        return False
    if payload.get("previous_response_id") not in (None, ""):
        return False
    if payload.get("prompt") not in (None, ""):
        return False
    if any(key not in _RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION for key in payload):
        return False
    if not _reasoning_config_is_account_neutral(payload.get("reasoning")):
        return False
    if not _tool_choice_is_account_neutral(payload.get("tool_choice")):
        return False
    if not _text_controls_are_account_neutral(payload.get("text")):
        return False
    if not _client_metadata_is_account_neutral(
        payload.get("client_metadata"),
        expected_session_identity=expected_session_identity,
        expected_task_identity=expected_task_identity,
    ):
        return False

    input_value = payload.get("input")
    if input_value is None or isinstance(input_value, str):
        input_items: list[JsonValue] = []
    elif isinstance(input_value, list):
        input_items = cast(list[JsonValue], input_value)
    else:
        return False
    if extract_input_file_ids(input_items):
        return False
    if any(
        isinstance(item, dict)
        and isinstance(item.get("type"), str)
        and item.get("type") not in _ACCOUNT_NEUTRAL_INPUT_ITEM_TYPES
        for item in input_items
    ):
        return False
    if not responses_input_items_are_self_contained_fresh_replay(input_items):
        return False
    if not _input_items_have_valid_account_neutral_shape(input_items):
        return False
    if _contains_account_scoped_input_state(input_items):
        return False

    tools = payload.get("tools")
    if tools is None:
        return True
    return _tools_are_account_neutral(tools)


def _reasoning_config_is_account_neutral(reasoning: JsonValue | None) -> bool:
    if reasoning is None:
        return True
    if not isinstance(reasoning, dict) or not set(reasoning) <= _ACCOUNT_NEUTRAL_REASONING_CONFIG_FIELDS:
        return False
    if "context" in reasoning and reasoning["context"] != "all_turns":
        return False
    return all(value is None or isinstance(value, str) for key, value in reasoning.items() if key != "context")


def _text_controls_are_account_neutral(text: JsonValue | None) -> bool:
    if text is None:
        return True
    if not isinstance(text, dict) or not set(text) <= {"format", "verbosity"}:
        return False
    verbosity = text.get("verbosity")
    if verbosity is not None and verbosity not in {"low", "medium", "high"}:
        return False
    format_value = text.get("format")
    if format_value is None:
        return True
    if not isinstance(format_value, dict):
        return False
    format_type = format_value.get("type")
    if format_type in {"text", "json_object"}:
        return set(format_value) == {"type"}
    if format_type != "json_schema" or not set(format_value) <= {
        "description",
        "name",
        "schema",
        "strict",
        "type",
    }:
        return False
    return (
        _is_nonblank_string(format_value.get("name"))
        and isinstance(format_value.get("schema"), dict)
        and (format_value.get("strict") is None or isinstance(format_value.get("strict"), bool))
        and (format_value.get("description") is None or isinstance(format_value.get("description"), str))
    )


def _client_metadata_is_account_neutral(
    client_metadata: JsonValue | None,
    *,
    expected_session_identity: str | None,
    expected_task_identity: str | None,
) -> bool:
    if client_metadata is None:
        return True
    if not isinstance(client_metadata, dict) or not set(client_metadata) <= _ACCOUNT_NEUTRAL_CLIENT_METADATA_FIELDS:
        return False
    if not all(_is_nonblank_string(value) for value in client_metadata.values()):
        return False
    if "x-codex-parent-thread-id" in client_metadata or "x-openai-subagent" in client_metadata:
        return False
    if (
        client_metadata.get(
            "ws_request_header_x_openai_internal_codex_responses_lite",
            "true",
        )
        != "true"
    ):
        return False
    session_id = client_metadata.get("session_id")
    thread_id = client_metadata.get("thread_id")
    turn_id = client_metadata.get("turn_id")
    root_turn_id = client_metadata.get("root_turn_id")
    if session_id is not None or thread_id is not None or turn_id is not None or root_turn_id is not None:
        if (
            expected_session_identity is None
            or expected_task_identity is None
            or session_id != expected_session_identity
            or thread_id != expected_task_identity
            or not _is_nonblank_string(turn_id)
            or (root_turn_id is not None and root_turn_id != turn_id)
        ):
            return False
    turn_metadata = client_metadata.get("x-codex-turn-metadata")
    if turn_metadata is None:
        return root_turn_id is None and session_id is None and thread_id is None and turn_id is None
    if session_id is None or thread_id is None or turn_id is None:
        return False
    evidence = account_neutral_codex_turn_metadata_identity(
        turn_metadata,
        carrier="body",
        expected_session_identity=expected_session_identity,
        expected_task_identity=expected_task_identity,
        expected_turn_identity=cast(str | None, turn_id),
    )
    if evidence is None:
        return False
    return (
        (root_turn_id is None or evidence.root_turn_identity == root_turn_id)
        and (
            "x-codex-installation-id" not in client_metadata
            or evidence.installation_identity == client_metadata["x-codex-installation-id"]
        )
        and (
            "x-codex-window-id" not in client_metadata
            or evidence.window_identity == client_metadata["x-codex-window-id"]
        )
    )


def account_neutral_codex_turn_metadata_identity(
    raw_turn_metadata: JsonValue,
    *,
    carrier: Literal["body", "direct"],
    expected_session_identity: str | None,
    expected_task_identity: str | None,
    expected_turn_identity: str | None,
) -> AccountNeutralCodexTurnMetadataEvidence | None:
    """Validate a canonical, root-task Codex 0.149 turn-metadata carrier."""

    max_bytes = (
        _ACCOUNT_NEUTRAL_TURN_METADATA_BODY_MAX_BYTES
        if carrier == "body"
        else _ACCOUNT_NEUTRAL_TURN_METADATA_DIRECT_MAX_BYTES
    )
    if (
        not isinstance(raw_turn_metadata, str)
        or not raw_turn_metadata.strip()
        or len(raw_turn_metadata.encode("utf-8")) > max_bytes
        or expected_session_identity is None
        or expected_task_identity is None
    ):
        return None
    try:
        decoded = json.loads(raw_turn_metadata)
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(decoded, dict)
        or not set(decoded) <= _ACCOUNT_NEUTRAL_TURN_METADATA_FIELDS
        or any(field in decoded for field in _ACCOUNT_NEUTRAL_TURN_METADATA_LINEAGE_FIELDS)
        or _contains_explicit_account_scoped_metadata_state(decoded)
        or (carrier == "direct" and "tool_namespaces_info" in decoded)
    ):
        return None

    session_id = decoded.get("session_id")
    thread_id = decoded.get("thread_id")
    turn_id = decoded.get("turn_id")
    if (
        session_id != expected_session_identity
        or thread_id != expected_task_identity
        or not _is_nonblank_string(turn_id)
        or (expected_turn_identity is not None and turn_id != expected_turn_identity)
        or decoded.get("request_kind") != "turn"
    ):
        return None
    for key in (
        "agent_name",
        "forked_from_thread_id",
        "installation_id",
        "sandbox",
        "sandbox_mode",
        "window_id",
    ):
        if key in decoded and not _is_nonblank_string(decoded[key]):
            return None
    if "workspace_kind" in decoded:
        workspace_kind = decoded["workspace_kind"]
        if (
            not _is_nonblank_string(workspace_kind)
            or len(cast(str, workspace_kind).encode("utf-8")) > _ACCOUNT_NEUTRAL_WORKSPACE_KIND_MAX_BYTES
        ):
            return None
    root_turn_id = decoded.get("root_turn_id")
    if root_turn_id is not None and root_turn_id != turn_id:
        return None
    if "thread_source" in decoded and not _root_thread_source_is_account_neutral(decoded["thread_source"]):
        return None
    for key in ("auto_review_enabled", "node_repl_auto_review_required", "node_repl_disabled"):
        if key in decoded and not isinstance(decoded[key], bool):
            return None
    started_at = decoded.get("turn_started_at_unix_ms")
    if started_at is not None and (not isinstance(started_at, int) or isinstance(started_at, bool)):
        return None
    if "workspaces" in decoded and not _turn_metadata_workspaces_are_account_neutral(decoded["workspaces"]):
        return None
    if "tool_namespaces_info" in decoded and not _turn_tool_namespaces_info_is_account_neutral(
        decoded["tool_namespaces_info"]
    ):
        return None
    shared_projection = dict(decoded)
    shared_projection.pop("tool_namespaces_info", None)
    shared_projection_fingerprint = sha256(
        json.dumps(
            shared_projection,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return AccountNeutralCodexTurnMetadataEvidence(
        session_identity=cast(str, session_id),
        task_identity=cast(str, thread_id),
        turn_identity=cast(str, turn_id),
        root_turn_identity=cast(str | None, root_turn_id),
        installation_identity=cast(str | None, decoded.get("installation_id")),
        window_identity=cast(str | None, decoded.get("window_id")),
        shared_projection_fingerprint=shared_projection_fingerprint,
    )


def _root_thread_source_is_account_neutral(value: JsonValue) -> bool:
    if isinstance(value, str):
        return value.lower() != "subagent" and bool(value.strip())
    if not isinstance(value, dict) or len(value) != 1:
        return False
    key, nested = next(iter(value.items()))
    return key.lower() != "subagent" and _is_nonblank_string(nested)


def _turn_metadata_workspaces_are_account_neutral(value: JsonValue) -> bool:
    if not isinstance(value, dict):
        return False
    for workspace, metadata in value.items():
        if (
            not _is_nonblank_string(workspace)
            or not isinstance(metadata, dict)
            or not set(metadata)
            <= {
                "associated_remote_urls",
                "has_changes",
                "latest_git_commit_hash",
            }
        ):
            return False
        urls = metadata.get("associated_remote_urls")
        if urls is not None and not (
            isinstance(urls, dict)
            and all(_is_nonblank_string(key) and _is_nonblank_string(url) for key, url in urls.items())
        ):
            return False
        if metadata.get("latest_git_commit_hash") is not None and not _is_nonblank_string(
            metadata["latest_git_commit_hash"]
        ):
            return False
        if metadata.get("has_changes") is not None and not isinstance(metadata["has_changes"], bool):
            return False
    return True


def _turn_tool_namespaces_info_is_account_neutral(value: JsonValue) -> bool:
    if not isinstance(value, dict):
        return False
    for effective_name, namespace in value.items():
        if (
            not _is_nonblank_string(effective_name)
            or not isinstance(namespace, dict)
            or set(namespace) != {"functions", "name"}
            or not _is_nonblank_string(namespace.get("name"))
            or not isinstance(namespace.get("functions"), dict)
            or not namespace.get("functions")
        ):
            return False
        for function_name, function in cast(dict[str, JsonValue], namespace["functions"]).items():
            if (
                not _is_nonblank_string(function_name)
                or not isinstance(function, dict)
                or set(function) != {"code_mode_name", "deferred", "direct", "name", "source"}
                or not _is_nonblank_string(function.get("name"))
                or not isinstance(function.get("direct"), bool)
                or not isinstance(function.get("deferred"), bool)
                or (
                    function.get("code_mode_name") is not None
                    and not _is_nonblank_string(function.get("code_mode_name"))
                )
                or not _turn_tool_source_is_account_neutral(function.get("source"))
            ):
                return False
    return True


def _turn_tool_source_is_account_neutral(value: JsonValue | None) -> bool:
    if not isinstance(value, dict) or value.get("kind") not in {"harness", "mcp"}:
        return False
    if value["kind"] == "harness":
        return set(value) == {"kind"}
    return set(value) == {"kind", "server_name"} and _is_nonblank_string(value.get("server_name"))


def _contains_explicit_account_scoped_metadata_state(value: JsonValue) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if _mapping_has_account_scoped_reference(current):
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _tools_are_account_neutral(tools: JsonValue) -> bool:
    return isinstance(tools, list) and all(
        isinstance(tool, dict) and _tool_declaration_is_account_neutral(tool) for tool in tools
    )


def _tool_declaration_is_account_neutral(tool: Mapping[str, JsonValue]) -> bool:
    tool_type = tool.get("type")
    if not isinstance(tool_type, str) or tool_type not in _ACCOUNT_NEUTRAL_TOOL_TYPES:
        return False
    if any(key not in _ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS[tool_type] for key in tool):
        return False
    if tool_type == "namespace":
        nested_tools = tool.get("tools")
        return (
            set(tool) == _ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS["namespace"]
            and _is_nonblank_string(tool.get("name"))
            and isinstance(tool.get("description"), str)
            and isinstance(nested_tools, list)
            and bool(nested_tools)
            and all(
                isinstance(nested_tool, dict) and _responses_lite_namespace_tool_is_account_neutral(nested_tool)
                for nested_tool in nested_tools
            )
        )
    if tool_type == "tool_search":
        return (
            set(tool) == _ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS["tool_search"]
            and tool.get("execution") == "client"
            and _is_nonblank_string(tool.get("description"))
            and _responses_lite_tool_search_schema_is_account_neutral(tool.get("parameters"))
        )
    if _contains_account_scoped_tool_state(tool):
        return False
    if tool_type in {"custom", "function"} and not _is_nonblank_string(tool.get("name")):
        return False
    if tool.get("description") is not None and not isinstance(tool.get("description"), str):
        return False
    if (
        tool_type in {"custom", "function"}
        and tool.get("defer_loading") is not None
        and not isinstance(tool.get("defer_loading"), bool)
    ):
        return False
    if tool_type == "function":
        return (tool.get("parameters") is None or isinstance(tool.get("parameters"), dict)) and (
            tool.get("strict") is None or isinstance(tool.get("strict"), bool)
        )
    if tool_type == "custom":
        return _custom_tool_format_is_account_neutral(tool.get("format"))
    return _web_search_tool_options_are_account_neutral(tool_type, tool)


def _responses_lite_namespace_tool_is_account_neutral(tool: Mapping[str, JsonValue]) -> bool:
    tool_type = tool.get("type")
    if tool_type == "function":
        required_fields = {"description", "name", "parameters", "strict", "type"}
        if not required_fields <= set(tool) or not set(tool) <= _ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS["function"]:
            return False
        return (
            _is_nonblank_string(tool.get("name"))
            and isinstance(tool.get("description"), str)
            and isinstance(tool.get("strict"), bool)
            and isinstance(tool.get("parameters"), dict)
            and tool.get("defer_loading", True) is True
        )
    if tool_type == "custom":
        required_fields = {"description", "format", "name", "type"}
        if not required_fields <= set(tool) or not set(tool) <= _ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS["custom"]:
            return False
        return (
            _is_nonblank_string(tool.get("name"))
            and isinstance(tool.get("description"), str)
            and _responses_lite_custom_tool_format_is_account_neutral(tool.get("format"))
            and tool.get("defer_loading", True) is True
        )
    return False


def _responses_lite_custom_tool_format_is_account_neutral(format_value: JsonValue | None) -> bool:
    """Validate the non-null FreeformToolFormat emitted inside a 0.149 namespace."""

    return (
        isinstance(format_value, dict)
        and format_value.get("type") == "grammar"
        and _custom_tool_format_is_account_neutral(format_value)
    )


def _responses_lite_tool_search_schema_is_account_neutral(value: JsonValue | None) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "additionalProperties",
        "properties",
        "required",
        "type",
    }:
        return False
    properties = value.get("properties")
    if (
        value.get("type") != "object"
        or value.get("required") != ["query"]
        or value.get("additionalProperties") is not False
        or not isinstance(properties, dict)
        or set(properties) != {"limit", "query"}
    ):
        return False
    query = properties.get("query")
    limit = properties.get("limit")
    return (
        isinstance(query, dict)
        and set(query) == {"description", "type"}
        and query.get("type") == "string"
        and _is_nonblank_string(query.get("description"))
        and isinstance(limit, dict)
        and set(limit) == {"description", "type"}
        and limit.get("type") == "number"
        and _is_nonblank_string(limit.get("description"))
    )


def _web_search_tool_options_are_account_neutral(
    tool_type: str,
    tool: Mapping[str, JsonValue],
) -> bool:
    filters = tool.get("filters")
    if filters is not None:
        if not isinstance(filters, dict) or not set(filters) <= _ACCOUNT_NEUTRAL_WEB_SEARCH_FILTER_FIELDS:
            return False
        allowed_domains = filters.get("allowed_domains")
        if allowed_domains is not None and not (
            isinstance(allowed_domains, list) and all(_is_nonblank_string(domain) for domain in allowed_domains)
        ):
            return False

    search_context_size = tool.get("search_context_size")
    if search_context_size is not None and search_context_size not in _ACCOUNT_NEUTRAL_WEB_SEARCH_CONTEXT_SIZES:
        return False

    user_location = tool.get("user_location")
    if user_location is None:
        return True
    if not isinstance(user_location, dict) or not set(user_location) <= _ACCOUNT_NEUTRAL_WEB_SEARCH_LOCATION_FIELDS:
        return False
    location_type = user_location.get("type")
    if location_type not in (None, "approximate") or (
        tool_type == "web_search_preview" and location_type != "approximate"
    ):
        return False
    return all(value is None or isinstance(value, str) for key, value in user_location.items() if key != "type")


def _tool_choice_is_account_neutral(tool_choice: JsonValue | None) -> bool:
    if tool_choice is None:
        return True
    if isinstance(tool_choice, str):
        return tool_choice in _ACCOUNT_NEUTRAL_TOOL_CHOICE_STRINGS
    if not isinstance(tool_choice, dict) or _contains_account_scoped_tool_state(tool_choice):
        return False
    choice_type = tool_choice.get("type")
    if choice_type in {"custom", "function"}:
        return set(tool_choice) <= {"name", "type"} and _is_nonblank_string(tool_choice.get("name"))
    if choice_type in {"web_search", "web_search_preview"}:
        return set(tool_choice) == {"type"}
    if choice_type != "allowed_tools" or set(tool_choice) > {"mode", "tools", "type"}:
        return False
    mode = tool_choice.get("mode")
    allowed = tool_choice.get("tools")
    return (
        mode in {"auto", "required"}
        and isinstance(allowed, list)
        and bool(allowed)
        and all(isinstance(tool, dict) and _tool_choice_reference_is_account_neutral(tool) for tool in allowed)
    )


def _tool_choice_reference_is_account_neutral(tool: Mapping[str, JsonValue]) -> bool:
    tool_type = tool.get("type")
    if tool_type in {"custom", "function"}:
        return set(tool) <= {"name", "type"} and _is_nonblank_string(tool.get("name"))
    return tool_type in {"web_search", "web_search_preview"} and set(tool) == {"type"}


def _custom_tool_format_is_account_neutral(format_value: JsonValue | None) -> bool:
    if format_value is None:
        return True
    if not isinstance(format_value, dict):
        return False
    format_type = format_value.get("type")
    if format_type == "text":
        return set(format_value) == {"type"}
    return (
        format_type == "grammar"
        and set(format_value) == {"definition", "syntax", "type"}
        and format_value.get("syntax") in {"lark", "regex"}
        and isinstance(format_value.get("definition"), str)
    )


def _contains_account_scoped_tool_state(value: JsonValue) -> bool:
    pending = [(value, True)]
    while pending:
        current, is_root = pending.pop()
        if isinstance(current, dict):
            if _mapping_has_account_scoped_reference(current):
                return True
            pending.extend(
                (nested, False)
                for key, nested in current.items()
                if not (is_root and current.get("type") == "function" and key == "parameters")
            )
        elif isinstance(current, list):
            pending.extend((nested, False) for nested in current)
    return False


def _input_items_have_valid_account_neutral_shape(input_items: list[JsonValue]) -> bool:
    for item in input_items:
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        if item_type in {"input_file", "input_image", "input_text"}:
            if not _input_content_part_is_self_contained(item, allow_output=False):
                return False
            continue
        if item_type == "additional_tools":
            if item.get("role") != "developer" or not _tools_are_account_neutral(item.get("tools")):
                return False
            continue
        if item_type not in (None, "message"):
            continue
        if not _message_has_valid_account_neutral_content(item):
            return False
    return True


def _message_has_valid_account_neutral_content(item: Mapping[str, JsonValue]) -> bool:
    role = item.get("role")
    if role not in _ACCOUNT_NEUTRAL_MESSAGE_ROLES:
        return False
    phase = item.get("phase")
    if phase is not None and phase not in {"commentary", "final_answer"}:
        return False
    content = item.get("content")
    if role != "assistant" and isinstance(content, str):
        return _is_nonblank_string(content)
    if not isinstance(content, list) or not content:
        return False
    if role == "assistant":
        return all(
            isinstance(part, dict)
            and part.get("type") in {"output_text", "refusal"}
            and _input_content_part_is_self_contained(part, allow_output=True)
            for part in content
        )
    return all(
        isinstance(part, dict) and _input_content_part_is_self_contained(part, allow_output=False) for part in content
    )


def _input_content_part_is_self_contained(
    part: Mapping[str, JsonValue],
    *,
    allow_output: bool,
) -> bool:
    part_type = part.get("type")
    if part_type not in _ACCOUNT_NEUTRAL_MESSAGE_CONTENT_TYPES:
        return False
    if any(key not in _ACCOUNT_NEUTRAL_CONTENT_FIELDS[cast(str, part_type)] for key in part):
        return False
    if part_type in {"input_text", "text"} or (allow_output and part_type == "output_text"):
        return _is_nonblank_string(part.get("text"))
    if allow_output and part_type == "refusal":
        return _is_nonblank_string(part.get("refusal"))
    if part_type == "input_image":
        return (part.get("detail") is None or isinstance(part.get("detail"), str)) and (
            _url_is_account_neutral(part.get("image_url"), allow_data=True) or _is_nonblank_string(part.get("file_id"))
        )
    if part_type == "input_file":
        return (part.get("filename") is None or isinstance(part.get("filename"), str)) and (
            _is_nonblank_string(part.get("file_data"))
            or _is_nonblank_string(part.get("file_id"))
            or _url_is_account_neutral(part.get("file_url"), allow_data=False)
        )
    return False


def _url_is_account_neutral(value: JsonValue | None, *, allow_data: bool) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return False
    return scheme in ({"data", "http", "https"} if allow_data else {"http", "https"})


def _contains_account_scoped_input_state(value: JsonValue) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            item_type = current.get("type")
            if isinstance(item_type, str) and item_type in _ACCOUNT_SCOPED_HOSTED_INPUT_TYPES:
                return True
            if isinstance(item_type, str) and item_type.startswith("mcp_"):
                return True
            if item_type == "additional_tools" and not _tools_are_account_neutral(current.get("tools")):
                return True
            if (
                isinstance(item_type, str)
                and (item_type.endswith("_call") or item_type.endswith("_call_output"))
                and item_type not in _TOOL_CALL_TYPES
                and item_type not in _TOOL_CALL_TYPE_BY_OUTPUT_TYPE
            ):
                return True
            if _mapping_has_account_scoped_reference(current):
                return True
            pending.extend(
                nested for key, nested in current.items() if not (item_type == "additional_tools" and key == "tools")
            )
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _mapping_has_account_scoped_reference(value: Mapping[str, JsonValue]) -> bool:
    for key in ("file_id", "container_id", "vector_store_id"):
        if value.get(key) not in (None, ""):
            return True
    if value.get("encrypted_content") not in (None, ""):
        return True
    for url_field, allow_data in (("image_url", True), ("file_url", False)):
        url_value = value.get(url_field)
        if url_value not in (None, "") and not _url_is_account_neutral(url_value, allow_data=allow_data):
            return True
    for key in ("file_ids", "vector_store_ids"):
        identifiers = value.get(key)
        if identifiers is not None and identifiers != []:
            return True
    return False
