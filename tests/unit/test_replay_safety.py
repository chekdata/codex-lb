from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.proxy.continuity import (
    is_http_bridge_account_neutral_replay,
    make_http_bridge_account_neutral_replay_key,
)
from app.modules.proxy.replay_safety import (
    abandoned_pending_agent_boundary_rejection_reason,
    project_responses_input_for_abandoned_pending_fresh_replay,
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_suffix_matches_pending_tool_calls,
    responses_input_suffix_proves_abandoned_pending_agent_boundary,
    responses_input_suffix_retains_prior_output,
    responses_payload_is_account_neutral_fresh_replay,
)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "input": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "name": "lookup"}],
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ]
        },
        {
            "input": [],
            "tools": [
                {
                    "type": "function",
                    "name": "inspect_file",
                    "parameters": {
                        "type": "object",
                        "properties": {"file_id": {"type": "string"}},
                    },
                }
            ],
        },
        {
            "input": [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_2",
                    "name": "shell",
                    "input": "pwd",
                    "caller": {"type": "direct"},
                },
                {"type": "custom_tool_call_output", "call_id": "call_2", "output": "/tmp"},
            ]
        },
        {
            "input": [
                {"type": "apply_patch_call", "call_id": "call_3", "patch": "*** Begin Patch\n*** End Patch"},
                {"type": "apply_patch_call_output", "call_id": "call_3", "output": "done"},
            ]
        },
        {
            "input": [
                {"type": "apply_patch_call", "call_id": "call_legacy_input", "input": "*** Begin Patch"},
                {
                    "type": "apply_patch_call_output",
                    "call_id": "call_legacy_input",
                    "output": "done",
                },
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_create",
                    "operation": {"type": "create_file", "path": "new.txt", "diff": "+new\n"},
                },
                {
                    "type": "apply_patch_call",
                    "call_id": "call_delete",
                    "operation": {"type": "delete_file", "path": "old.txt"},
                },
                {
                    "type": "apply_patch_call",
                    "call_id": "call_update",
                    "operation": {"type": "update_file", "path": "app.py", "diff": "@@\n-old\n+new\n"},
                },
                {"type": "apply_patch_call_output", "call_id": "call_create", "output": "created"},
                {"type": "apply_patch_call_output", "call_id": "call_delete", "output": "deleted"},
                {"type": "apply_patch_call_output", "call_id": "call_update", "output": "updated"},
            ]
        },
        {"input": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]},
        {"input": [{"type": "input_file", "file_url": "https://example.com/context.txt"}]},
        {"input": [], "reasoning": {"effort": "high", "summary": "auto"}},
        {
            "input": [],
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            },
        },
        {
            "input": [],
            "client_metadata": {
                "x-codex-window-id": "child:0",
                "ws_request_header_x_openai_internal_codex_responses_lite": "true",
            },
        },
        {
            "input": [],
            "tools": [{"type": "function", "name": "lookup"}, {"type": "web_search"}],
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "function", "name": "lookup"}, {"type": "web_search"}],
            },
        },
        {
            "input": [],
            "tools": [
                {
                    "type": "web_search",
                    "filters": {"allowed_domains": ["openai.com"]},
                    "search_context_size": "high",
                    "user_location": {
                        "type": "approximate",
                        "city": "San Francisco",
                        "country": "US",
                    },
                }
            ],
        },
    ],
)
def test_account_neutral_fresh_replay_accepts_self_contained_payloads(
    payload: dict[str, JsonValue],
) -> None:
    assert responses_payload_is_account_neutral_fresh_replay(payload) is True


def test_account_neutral_replay_projection_removes_response_owned_bookkeeping() -> None:
    metadata = {"turn_id": "turn_owner_a"}
    input_items: list[JsonValue] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first question"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "reasoning",
            "id": "rs_owner_a",
            "encrypted_content": "owner-a-ciphertext",
            "summary": [],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call",
            "id": "ctc_owner_a",
            "call_id": "call_boundary",
            "name": "exec",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_owner_a",
            "call_id": "call_boundary",
            "output": "/workspace",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "tool_search_call",
            "id": "tsc_owner_a",
            "call_id": "call_search",
            "arguments": {"query": "github"},
            "execution": "client",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "execution": "client",
            "status": "completed",
            "tools": [],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "web_search_call",
            "id": "ws_owner_a",
            "action": {"type": "search", "query": "github"},
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "id": "msg_owner_a",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_next"},
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(input_items, stored_count=3)

    assert projection is not None
    assert projection.stored_prefix_count == 2
    assert projection.input_items == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first question"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call",
            "call_id": "call_boundary",
            "name": "exec",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_boundary",
            "output": "/workspace",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
            "internal_chat_message_metadata_passthrough": metadata,
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_next"},
        },
    ]
    assert (
        responses_input_suffix_retains_prior_output(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
        )
        is True
    )
    assert responses_payload_is_account_neutral_fresh_replay({"input": projection.input_items}) is True


def test_account_neutral_replay_projection_rejects_invalid_stored_boundary() -> None:
    assert (
        project_responses_input_for_account_neutral_fresh_replay(
            [{"role": "user", "content": "hello"}],
            stored_count=2,
        )
        is None
    )


@pytest.mark.parametrize(
    "search_item",
    [
        {
            "type": "web_search_call",
            "id": "ws_in_progress",
            "action": {"type": "search", "query": "pending"},
            "status": "in_progress",
        },
        {
            "type": "tool_search_call",
            "id": "tsc_in_progress",
            "call_id": "call_search",
            "arguments": {"query": "pending"},
            "status": "in_progress",
        },
        {
            "type": "tool_search_output",
            "call_id": "call_search",
            "status": "incomplete",
            "tools": [],
        },
    ],
)
def test_account_neutral_replay_projection_preserves_noncompleted_search_state_to_fail_closed(
    search_item: dict[str, JsonValue],
) -> None:
    input_items: list[JsonValue] = [
        {"role": "user", "content": "old question"},
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "old answer"}],
        },
        search_item,
        {"role": "user", "content": "next question"},
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(input_items, stored_count=1)

    assert projection is not None
    assert any(isinstance(item, dict) and item.get("type") == search_item["type"] for item in projection.input_items)
    assert responses_payload_is_account_neutral_fresh_replay({"input": projection.input_items}) is False


@pytest.mark.parametrize(
    "suffix",
    [
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "prior answer"}],
            },
            {"role": "user", "content": "next question"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "tool result summarized"}],
            },
            {"role": "user", "content": "next question"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "first retained answer"}],
            },
            {"role": "user", "content": "intermediate question"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "second retained answer"}],
            },
            {"role": "user", "content": "current question"},
        ],
    ],
)
def test_full_resend_suffix_requires_prior_output_before_new_input(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]

    assert (
        responses_input_suffix_retains_prior_output(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
        )
        is True
    )


@pytest.mark.parametrize(
    "suffix",
    [
        [{"role": "user", "content": "next question"}],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "prior answer"}],
            }
        ],
        [
            {"role": "user", "content": "next question"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "late output"}],
            },
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_unresolved",
                "name": "lookup",
                "arguments": "{}",
            },
            {"role": "user", "content": "skip the tool"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_late",
                "name": "lookup",
                "arguments": "{}",
            },
            {"role": "user", "content": "answer before the tool result"},
            {"type": "function_call_output", "call_id": "call_late", "output": "late"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_new",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_old", "output": "stale"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "partial"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_2", "output": "second"},
            {"type": "function_call_output", "call_id": "call_1", "output": "first"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "tool results summarized"}],
            },
            {"role": "user", "content": "next question"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_only",
                "name": "lookup",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_only", "output": "result"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_duplicate",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "call_id": "call_duplicate",
                "name": "lookup_again",
                "arguments": "{}",
            },
            {"type": "function_call_output", "call_id": "call_duplicate", "output": "ambiguous"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [{"type": "output_text", "text": "partial answer"}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "answer"},
                    {"type": "input_text", "text": "role-invalid"},
                ],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "answer"}],
            },
            {
                "role": "user",
                "content": [{"type": "output_text", "text": "role-invalid"}],
            },
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_partial",
                "name": "lookup",
                "arguments": "{}",
                "status": "in_progress",
            },
            {"type": "function_call_output", "call_id": "call_partial", "output": "result"},
        ],
        [
            {
                "type": "function_call",
                "call_id": "call_partial_output",
                "name": "lookup",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_partial_output",
                "output": "partial",
                "status": "in_progress",
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "not settled"}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": ""}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "refusal", "refusal": "   "}],
            },
            {"role": "user", "content": "continue"},
        ],
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "prior answer"}],
            },
            {"role": "user", "content": ""},
        ],
    ],
)
def test_full_resend_suffix_rejects_missing_or_misordered_context(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]

    assert (
        responses_input_suffix_retains_prior_output(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
        )
        is False
    )


@pytest.mark.parametrize(
    ("suffix", "pending_tool_calls", "expected"),
    [
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            ],
            {"call_1": "function_call"},
            True,
            id="complete-tool-loop",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "custom_tool_call",
                    "call_id": "call_2",
                    "name": "shell",
                    "input": "pwd",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
                {"type": "custom_tool_call_output", "call_id": "call_2", "output": "/workspace"},
            ],
            {"call_1": "function_call", "call_2": "custom_tool_call"},
            True,
            id="complete-parallel-tool-loop",
        ),
        pytest.param(
            [{"role": "user", "content": "revise that answer"}],
            {"call_1": "function_call"},
            False,
            id="missing-output",
        ),
        pytest.param(
            [{"type": [], "call_id": "call_1"}],
            {"call_1": "function_call"},
            False,
            id="malformed-item-type",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                }
            ],
            {"call_1": "function_call"},
            False,
            id="unsettled-call",
        ),
        pytest.param(
            [{"type": "function_call_output", "call_id": "call_1", "output": "orphan"}],
            {"call_1": "function_call"},
            False,
            id="orphan-output",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "partial"},
            ],
            {"call_1": "function_call", "call_2": "function_call"},
            False,
            id="omitted-parallel-call",
        ),
        pytest.param(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {"role": "developer", "content": "new control message"},
                {"type": "function_call_output", "call_id": "call_1", "output": "result"},
            ],
            {"call_1": "function_call"},
            False,
            id="inline-developer-message-in-fresh-suffix",
        ),
    ],
)
def test_full_resend_suffix_accepts_only_self_contained_tool_loops(
    suffix: list[JsonValue],
    pending_tool_calls: dict[str, str],
    expected: bool,
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls=pending_tool_calls,
        )
        is expected
    )


def test_full_resend_tool_loop_manifest_tolerates_fresh_developer_interleave_after_historical_one() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "id": "ctc_old",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
            "content": [{"type": "input_text", "text": "historical control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_old",
            "call_id": "call_old",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "id": "ctc_current",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_current",
            "call_id": "call_current",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )
    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is True
    )


def test_full_resend_tool_loop_manifest_rejects_response_owned_historical_developer() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "id": "ctc_old",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
        {
            "type": "message",
            "id": "msg_old_control",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
            "content": [{"type": "input_text", "text": "historical control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_old",
            "call_id": "call_old",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_old"},
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "id": "ctc_current",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_current",
            "call_id": "call_current",
            "output": "/workspace",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )
    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is False
    )


def test_full_resend_tool_loop_manifest_rejects_response_owned_fresh_developer() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "message",
            "id": "msg_response_owned",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "/workspace",
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls={"call_current": "custom_tool_call"},
    )


def test_full_resend_tool_loop_manifest_rejects_fresh_developer_inside_function_call_pair() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_current",
            "name": "lookup",
            "arguments": "{}",
        },
        {
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
            "content": [{"type": "input_text", "text": "unobserved function control"}],
        },
        {
            "type": "function_call_output",
            "call_id": "call_current",
            "output": "ok",
        },
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "function_call"},
        )
        is False
    )


def test_full_resend_tool_loop_manifest_rejects_adjacent_pair_nested_in_parallel_batch() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {"type": "custom_tool_call", "call_id": "call_1", "name": "shell", "input": "pwd"},
        {"type": "custom_tool_call", "call_id": "call_2", "name": "shell", "input": "whoami"},
        {
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
            "content": [{"type": "input_text", "text": "control inside one matching parallel pair"}],
        },
        {"type": "custom_tool_call_output", "call_id": "call_2", "output": "worker"},
        {"type": "custom_tool_call_output", "call_id": "call_1", "output": "/workspace"},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_1": "custom_tool_call", "call_2": "custom_tool_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(
            [
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control before call"}],
                },
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-before-call",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after output"}],
                },
            ],
            id="developer-after-output",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "first control"}],
                },
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "second control"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="multiple-developer-messages",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "control without turn id"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-without-turn-id",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "type": None,
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control with null type"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-with-null-type",
        ),
        pytest.param(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "working"}],
                },
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
            ],
            id="developer-with-leading-assistant-exception",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_current", "name": "shell", "input": "pwd"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control"}],
                },
                {"type": "custom_tool_call_output", "call_id": "call_current", "output": "/workspace"},
                {"role": "user", "content": "follow up"},
            ],
            id="developer-with-trailing-user-exception",
        ),
    ],
)
def test_full_resend_tool_loop_manifest_rejects_unproven_fresh_developer_positions(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    ("interleaved_item", "expected"),
    [
        pytest.param(
            {"role": "developer", "content": "historical control"},
            True,
            id="implicit-developer-message",
        ),
        pytest.param(
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "historical control"}],
            },
            True,
            id="explicit-developer-message",
        ),
        pytest.param(
            {"type": None, "role": "developer", "content": "historical control"},
            False,
            id="null-type-developer-message",
        ),
        pytest.param(
            {
                "type": "bogus",
                "role": "developer",
                "id": "msg_owned",
                "content": "historical control",
            },
            False,
            id="malformed-type-response-owned-developer-message",
        ),
        pytest.param(
            {
                "type": "reasoning",
                "role": "developer",
                "content": "projection-omitted control",
            },
            False,
            id="projection-omitted-developer-message",
        ),
        pytest.param(
            {
                "role": "developer",
                "status": "completed",
                "internal_chat_message_metadata_passthrough": {"turn_id": "turn_historical"},
                "content": "historical control",
            },
            True,
            id="completed-developer-message-with-neutral-metadata",
        ),
        pytest.param(
            {"role": "developer", "id": "msg_owned", "content": "historical control"},
            False,
            id="response-owned-developer-message",
        ),
        pytest.param(
            {"role": "developer", "id": "", "content": "historical control"},
            False,
            id="empty-response-owned-developer-message",
        ),
        pytest.param(
            {"role": "developer", "content": "   "},
            False,
            id="malformed-developer-message",
        ),
        pytest.param(
            {"role": "developer", "phase": "commentary", "content": "historical control"},
            False,
            id="phased-developer-message",
        ),
        pytest.param(
            {"role": "developer", "status": "failed", "content": "historical control"},
            False,
            id="failed-developer-message",
        ),
        pytest.param(
            {"role": "developer", "content": "historical control", "account_id": "account-scoped"},
            False,
            id="unknown-developer-field",
        ),
        pytest.param(
            {
                "role": "developer",
                "content": "historical control",
                "internal_chat_message_metadata_passthrough": {"turn_id": ""},
            },
            False,
            id="invalid-developer-metadata",
        ),
        pytest.param(
            {"role": "user", "content": "new user input"},
            False,
            id="user-message",
        ),
        pytest.param(
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "assistant output"}],
            },
            False,
            id="assistant-message",
        ),
        pytest.param(
            {"role": "system", "content": "system control"},
            False,
            id="system-message",
        ),
    ],
)
def test_full_resend_exact_manifest_only_allows_historical_developer_interleaving(
    interleaved_item: JsonValue,
    expected: bool,
) -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
        },
        interleaved_item,
        {
            "type": "custom_tool_call_output",
            "call_id": "call_old",
            "output": "/workspace",
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    input_items = [*stored_input, *suffix]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is expected
    )


@pytest.mark.parametrize(
    "stored_prefix_tail",
    [
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="parallel-batch-around-interleaved-developer",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="developer-inside-window-that-held-parallel-calls",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "message", "role": "developer", "content": "first historical control message"},
                {"type": "message", "role": "developer", "content": "second historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
            ],
            id="duplicate-interleaved-developer-messages",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="parallel-call-opened-after-interleaved-developer",
        ),
    ],
)
def test_full_resend_exact_manifest_rejects_unbounded_historical_developer_interleave(
    stored_prefix_tail: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}, *stored_prefix_tail]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    "stored_prefix_tail",
    [
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "message", "role": "developer", "content": "first historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "message", "role": "developer", "content": "second historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
            ],
            id="sequential-single-call-windows",
        ),
        pytest.param(
            [
                {"type": "custom_tool_call", "call_id": "call_first", "name": "shell", "input": "pwd"},
                {"type": "custom_tool_call", "call_id": "call_second", "name": "shell", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "call_first", "output": "/workspace"},
                {"type": "custom_tool_call_output", "call_id": "call_second", "output": "README.md"},
                {"type": "custom_tool_call", "call_id": "call_third", "name": "shell", "input": "git status"},
                {"type": "message", "role": "developer", "content": "historical control message"},
                {"type": "custom_tool_call_output", "call_id": "call_third", "output": ""},
            ],
            id="clean-window-after-drained-parallel-window",
        ),
    ],
)
def test_full_resend_exact_manifest_accepts_bounded_historical_developer_windows(
    stored_prefix_tail: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}, *stored_prefix_tail]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert (
        responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls={"call_current": "custom_tool_call"},
        )
        is True
    )


def test_full_resend_exact_manifest_accepts_canonical_lite_prefix_developer_without_pending_call() -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [{"type": "custom", "name": "shell"}],
        },
        {"type": "message", "role": "developer", "content": "stored control"},
        {"role": "user", "content": "first question"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "/workspace",
        },
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert projection.canonical_lite_developer_index == 1
    assert responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls={"call_current": "custom_tool_call"},
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "stored_input",
    [
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="nonadjacent-unowned-developer",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {
                    "type": "message",
                    "id": "msg_owned",
                    "role": "developer",
                    "content": "stored control",
                },
                {"role": "user", "content": "first question"},
            ],
            id="response-owned-canonical-position",
        ),
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="bundle-not-at-prefix-start",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "reasoning", "id": "rs_hidden", "summary": []},
                {"type": "message", "role": "developer", "content": "stored control"},
                {"role": "user", "content": "first question"},
            ],
            id="projection-collapsed-adjacency",
        ),
    ],
)
def test_full_resend_exact_manifest_rejects_unverified_stored_developer_without_pending_call(
    stored_input: list[JsonValue],
) -> None:
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "/workspace",
        },
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls={"call_current": "custom_tool_call"},
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "stored_input",
    [
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="bundle-not-at-prefix-start",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {"type": "reasoning", "id": "rs_hidden", "summary": []},
                {"type": "message", "role": "developer", "content": "stored control"},
                {"role": "user", "content": "first question"},
            ],
            id="projection-collapsed-adjacency",
        ),
    ],
)
def test_full_resend_retained_output_rejects_noncanonical_stored_lite_developer(
    stored_input: list[JsonValue],
) -> None:
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert projection.canonical_lite_developer_index is None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "historical_output",
    [
        pytest.param(None, id="missing-output"),
        pytest.param(
            {
                "type": "custom_tool_call_output",
                "call_id": "call_other",
                "output": "/workspace",
            },
            id="mismatched-output",
        ),
    ],
)
def test_full_resend_exact_manifest_requires_historical_interleaved_call_output(
    historical_output: JsonValue | None,
) -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
        },
        {"role": "developer", "content": "historical control"},
    ]
    if historical_output is not None:
        stored_input.append(historical_output)
    suffix: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_current",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_current",
            "output": "",
        },
    ]

    assert not responses_input_suffix_matches_pending_tool_calls(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        pending_tool_calls={"call_current": "custom_tool_call"},
    )


def test_full_resend_retained_output_tolerates_fresh_developer_after_user() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "id": "msg_answer",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_previous"},
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {
            "type": "message",
            "id": "msg_user",
            "role": "user",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "next question"}],
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )
    assert projection is not None
    assert (
        responses_input_suffix_retains_prior_output(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
        )
        is True
    )


def test_full_resend_retained_output_accepts_canonical_lite_prefix_developer_without_pending_call() -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [{"type": "custom", "name": "shell"}],
        },
        {"type": "message", "role": "developer", "content": "stored control"},
        {"role": "user", "content": "first question"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert projection.canonical_lite_developer_index == 1
    assert responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


@pytest.mark.parametrize(
    "stored_input",
    [
        pytest.param(
            [
                {"role": "user", "content": "first question"},
                {"type": "message", "role": "developer", "content": "stored control"},
            ],
            id="nonadjacent-unowned-developer",
        ),
        pytest.param(
            [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [{"type": "custom", "name": "shell"}],
                },
                {
                    "type": "message",
                    "id": "msg_owned",
                    "role": "developer",
                    "content": "stored control",
                },
                {"role": "user", "content": "first question"},
            ],
            id="response-owned-canonical-position",
        ),
    ],
)
def test_full_resend_retained_output_rejects_unverified_stored_developer_without_pending_call(
    stored_input: list[JsonValue],
) -> None:
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    )


def test_full_resend_retained_output_accepts_exact_settled_same_session_prefix() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_historical",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_historical",
            "output": "/workspace",
        },
        {"type": "message", "role": "developer", "content": "later stored control"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next question"}]},
    ]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
    )
    assert responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        exact_stored_prefix_without_pending_manifest=True,
    )


def _canonical_agent_message() -> dict[str, JsonValue]:
    return {
        "type": "agent_message",
        "id": "amsg_01a02b33-3b30-7742-bdb3-091f07cf2ea0",
        "author": "/root/episode_identity_final_audit",
        "recipient": "/root",
        "internal_chat_message_metadata_passthrough": {
            "turn_id": "01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
            "create_time": 1787431172.912141,
        },
        "content": [{"type": "input_text", "text": "verified inter-agent result"}],
    }


def _canonical_response_owned_user_message(
    *,
    message_id: str = "msg_01a02c43-4980-7afb-97f5-2e2d30aa73de",
    turn_id: str = "01a02c43-4980-7afb-97f5-2e2d30aa73de",
    text: str = "retry",
) -> dict[str, JsonValue]:
    return {
        "type": "message",
        "id": message_id,
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {
            "turn_id": turn_id,
            "create_time": 1787433402.605,
        },
    }


def _canonical_response_owned_developer_message() -> dict[str, JsonValue]:
    return {
        "type": "message",
        "id": "msg_01a02a78-d2b5-71e3-a33e-fab25a40b322",
        "role": "developer",
        "content": [
            {"type": "input_text", "text": "stored permissions"},
            {"type": "input_text", "text": "stored app context"},
            {"type": "input_text", "text": "stored collaboration mode"},
            {"type": "input_text", "text": "stored skills"},
        ],
        "internal_chat_message_metadata_passthrough": {
            "turn_id": "01a02a78-d2b4-7a73-b03f-6d85db9cf496",
        },
    }


def _rehydrate_sanitized_pending_settlement_shapes() -> list[tuple[list[JsonValue], int, dict[str, str]]]:
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "http_responses" / "pending_settlement_real_transport_shapes_v1.json"
    )
    fixture_bytes = fixture_path.read_bytes()
    assert (
        hashlib.sha256(fixture_bytes).hexdigest() == "c9a5eedac589f973c30ed100cb429d60d8e2b9db439dd75f747b4081c09f2630"
    )
    fixture = json.loads(fixture_bytes)
    assert fixture["schema"] == "qk_http_responses_pending_settlement_shape_fixture_v1"
    assert fixture["provenance"] == {
        "contains_call_ids": False,
        "contains_credentials": False,
        "contains_message_text": False,
        "contains_raw_ids": False,
        "sanitized": True,
        "source_kind": "production_request_structure",
    }
    hydrated: list[tuple[list[JsonValue], int, dict[str, str]]] = []
    for case_index, case in enumerate(fixture["cases"]):
        stored_count = case["stored_count"]
        input_items: list[JsonValue] = [
            {"role": "user", "content": f"sanitized-stored-{index}"} for index in range(stored_count)
        ]
        call_id = f"fixture-pending-{case_index}"
        for shape in case["suffix_shape"]:
            if shape == "reasoning":
                input_items.append(
                    {
                        "type": "reasoning",
                        "id": f"rs_fixture_{case_index}",
                        "content": None,
                        "encrypted_content": "fixture-ciphertext",
                        "summary": [],
                    }
                )
            elif shape == "custom_tool_call":
                input_items.append(
                    {
                        "type": "custom_tool_call",
                        "id": f"ctc_fixture_{case_index}",
                        "call_id": call_id,
                        "name": "fixture_tool",
                        "input": "fixture-input",
                        "status": "completed",
                    }
                )
            elif shape == "custom_tool_call_output":
                input_items.append(
                    {
                        "type": "custom_tool_call_output",
                        "id": f"ctco_fixture_{case_index}",
                        "call_id": call_id,
                        "output": "fixture-output",
                    }
                )
            elif shape == "function_call":
                input_items.append(
                    {
                        "type": "function_call",
                        "id": f"fc_fixture_{case_index}",
                        "call_id": call_id,
                        "name": "fixture_tool",
                        "arguments": "{}",
                    }
                )
            elif shape == "function_call_output":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "id": f"fco_fixture_{case_index}",
                        "call_id": call_id,
                        "output": "fixture-output",
                    }
                )
            elif shape == "agent_message":
                input_items.append(_canonical_agent_message())
            elif shape == "user_message":
                input_items.append(
                    _canonical_response_owned_user_message(
                        message_id=f"msg_{UUID(int=case_index + 100)}",
                        turn_id=f"{UUID(int=case_index + 100)}",
                        text="sanitized-followup",
                    )
                )
            else:  # pragma: no cover - fixture schema is closed above
                raise AssertionError(f"unknown pending-settlement fixture shape: {shape}")
        hydrated.append((input_items, stored_count, {call_id: case["pending_type"]}))
    return hydrated


def test_real_sanitized_pending_settlement_shapes_accept_exact_manifest_then_bounded_followups() -> None:
    cases = _rehydrate_sanitized_pending_settlement_shapes()

    assert len(cases) == 3
    for input_items, stored_count, pending_tool_calls in cases:
        projection = project_responses_input_for_account_neutral_fresh_replay(
            input_items,
            stored_count=stored_count,
            preserve_developer_message_ids=True,
            preserve_response_owned_agent_message_ids=True,
        )
        assert projection is not None
        assert responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls=pending_tool_calls,
        )


def test_pending_settlement_followup_never_treats_unrelated_call_as_the_pending_call() -> None:
    input_items, stored_count, pending_tool_calls = _rehydrate_sanitized_pending_settlement_shapes()[0]
    pending_call_id = next(iter(pending_tool_calls))
    output_index = next(
        index
        for index, item in enumerate(input_items)
        if isinstance(item, dict)
        and item.get("call_id") == pending_call_id
        and str(item.get("type", "")).endswith("_output")
    )
    input_items[output_index] = {
        "type": "custom_tool_call_output",
        "call_id": "fixture-unrelated-call",
        "output": "fixture-output",
    }
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=stored_count,
        preserve_developer_message_ids=True,
        preserve_response_owned_agent_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls=pending_tool_calls,
    )


def test_pending_settlement_followup_rejects_a_second_tool_loop_after_exact_settlement() -> None:
    input_items, stored_count, pending_tool_calls = _rehydrate_sanitized_pending_settlement_shapes()[0]
    input_items.extend(
        [
            {
                "type": "custom_tool_call",
                "call_id": "fixture-unrelated-call",
                "name": "fixture_tool",
                "input": "fixture-input",
            },
            {
                "type": "custom_tool_call_output",
                "call_id": "fixture-unrelated-call",
                "output": "fixture-output",
            },
        ]
    )
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=stored_count,
        preserve_developer_message_ids=True,
        preserve_response_owned_agent_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls=pending_tool_calls,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-output",
        "duplicate-output",
        "output-before-call",
        "pending-type-drift",
        "call-status-drift",
        "malformed-agent-message",
        "developer-before-user",
    ],
)
def test_pending_settlement_followup_rejects_inexact_or_unbounded_shapes(mutation: str) -> None:
    original, stored_count, original_manifest = _rehydrate_sanitized_pending_settlement_shapes()[0]
    input_items = cast(list[JsonValue], json.loads(json.dumps(original)))
    pending_tool_calls = dict(original_manifest)
    call_id = next(iter(pending_tool_calls))
    call_index = next(
        index
        for index, item in enumerate(input_items)
        if isinstance(item, dict) and item.get("call_id") == call_id and item.get("type") == "custom_tool_call"
    )
    output_index = next(
        index
        for index, item in enumerate(input_items)
        if isinstance(item, dict) and item.get("call_id") == call_id and item.get("type") == "custom_tool_call_output"
    )
    if mutation == "missing-output":
        input_items.pop(output_index)
    elif mutation == "duplicate-output":
        input_items.insert(
            output_index + 1, cast(JsonValue, dict(cast(dict[str, JsonValue], input_items[output_index])))
        )
    elif mutation == "output-before-call":
        input_items[call_index], input_items[output_index] = input_items[output_index], input_items[call_index]
    elif mutation == "pending-type-drift":
        pending_tool_calls[call_id] = "function_call"
    elif mutation == "call-status-drift":
        cast(dict[str, JsonValue], input_items[call_index])["status"] = "in_progress"
    elif mutation == "malformed-agent-message":
        input_items[-1] = _canonical_agent_message()
        input_items[-1]["extra"] = "unbound"
        input_items.append({"role": "user", "content": "sanitized-followup"})
    elif mutation == "developer-before-user":
        input_items[-1] = {"role": "developer", "content": "sanitized-control"}
        input_items.append({"role": "user", "content": "sanitized-followup"})
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(mutation)
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=stored_count,
        preserve_developer_message_ids=True,
        preserve_response_owned_agent_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_matches_pending_tool_calls(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        pending_tool_calls=pending_tool_calls,
    )


def _rehydrate_sanitized_abandoned_pending_fixture() -> tuple[list[JsonValue], int, dict[str, str]]:
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "http_responses" / "abandoned_pending_real_transport_shape_v1.json"
    )
    fixture_bytes = fixture_path.read_bytes()
    assert (
        hashlib.sha256(fixture_bytes).hexdigest() == "272d9d38876e409ca0ddf5e1c1990bd603347dffb3a33a3e43be5d82d77a9932"
    )
    fixture = json.loads(fixture_bytes)
    assert fixture["schema"] == "qk_http_responses_sanitized_shape_fixture_v1"
    assert fixture["provenance"] == {
        "contains_call_ids": False,
        "contains_credentials": False,
        "contains_message_text": False,
        "contains_raw_ids": False,
        "sanitized": True,
        "source_kind": "production_request_structure",
    }
    request = fixture["request"]
    assert request["top_keys"] == [
        "client_metadata",
        "include",
        "input",
        "model",
        "parallel_tool_calls",
        "prompt_cache_key",
        "reasoning",
        "store",
        "stream",
        "text",
        "tool_choice",
    ]
    catalog = request["item_shape_catalog"]
    sequence = request["item_shape_sequence"]
    assert len(sequence) == request["item_count"] == 143

    items: list[JsonValue] = []
    preceding_call_id: str | None = None
    for index, shape_id in enumerate(sequence):
        shape = catalog[shape_id]
        item_type = shape["type"]
        role = shape["role"]
        item: dict[str, Any]
        if item_type == "additional_tools":
            item = {
                "type": "additional_tools",
                "role": "developer",
                "tools": [
                    {
                        "type": "custom",
                        "name": "fixture_tool",
                        "description": "fixture-only tool declaration",
                        "format": {"type": "text"},
                    }
                ],
            }
        elif item_type == "reasoning":
            item = {
                "type": "reasoning",
                "id": f"rs_fixture_{index}",
                "content": None,
                "encrypted_content": f"fixture-ciphertext-{index}",
                "summary": [],
            }
        elif item_type == "agent_message":
            item = {
                "type": "agent_message",
                "id": f"amsg_{UUID(int=index + 1)}",
                "author": "/root/fixture_worker",
                "recipient": "/root",
                "content": [{"type": "input_text", "text": f"fixture-item-{index}-part-0"}],
            }
        elif item_type == "message":
            content_types = shape["content_types"]
            assert isinstance(content_types, list)
            item = {
                "type": "message",
                "role": role,
                "content": [
                    {"type": content_type, "text": f"fixture-item-{index}-part-{part_index}"}
                    for part_index, content_type in enumerate(content_types)
                ],
            }
            if "id" in shape["known_keys"]:
                item["id"] = f"msg_{UUID(int=index + 1)}"
            if shape["phase"] is not None:
                item["phase"] = shape["phase"]
        elif item_type in {"function_call", "custom_tool_call"}:
            preceding_call_id = f"fixture-call-{index}"
            item = {
                "type": item_type,
                "id": f"fixture-call-item-{index}",
                "call_id": preceding_call_id,
                "name": "fixture_tool",
            }
            if item_type == "function_call":
                item["namespace"] = "fixture_namespace"
                item["arguments"] = "{}"
            else:
                item["input"] = f"fixture-input-{index}"
            if shape["status"] is not None:
                item["status"] = shape["status"]
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            assert preceding_call_id is not None
            item = {
                "type": item_type,
                "id": f"fixture-output-item-{index}",
                "call_id": preceding_call_id,
                "output": f"fixture-output-{index}",
            }
            preceding_call_id = None
        else:  # pragma: no cover - fixture schema is closed above
            raise AssertionError(f"unknown sanitized shape: {item_type}")
        assert set(item) == set(shape["known_keys"])
        items.append(cast(JsonValue, item))

    assert preceding_call_id is None
    pending_tool_calls = {"fixture-call-undelivered": fixture["pending_tool_calls"]["types"][0]}
    return items, request["stored_count"], pending_tool_calls


def test_real_sanitized_transport_shape_recovers_once_without_duplicate_or_context_loss() -> None:
    input_items, stored_count, pending_tool_calls = _rehydrate_sanitized_abandoned_pending_fixture()

    # This exact production shape was rejected by the legacy proof because
    # the HTTP transport retained response-owned IDs but stripped their
    # internal metadata, and because one developer refresh followed the user
    # retry run. The fixture intentionally contains neither message bodies nor
    # raw production identifiers.
    response_owned_suffix = input_items[stored_count:]
    assert all(
        not isinstance(item, dict) or "internal_chat_message_metadata_passthrough" not in item
        for item in response_owned_suffix
    )
    assert any(isinstance(item, dict) and item.get("role") == "developer" for item in response_owned_suffix)
    legacy_metadata_required_items = [input_items[index] for index in (111, 112, 113, 140)]
    legacy_boundary_proof_would_accept = all(
        isinstance(item, dict) and isinstance(item.get("internal_chat_message_metadata_passthrough"), dict)
        for item in legacy_metadata_required_items
    )
    assert legacy_boundary_proof_would_accept is False

    parsed_payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "", "input": input_items})
    assert isinstance(parsed_payload.input, list)
    parsed_input = parsed_payload.input
    assert len(parsed_input) == 143
    assert isinstance(parsed_input[140], dict) and parsed_input[140].get("role") == "developer"
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            parsed_input,
            stored_count=stored_count,
            pending_tool_calls=pending_tool_calls,
        )
        is None
    )
    projection = project_responses_input_for_abandoned_pending_fresh_replay(
        parsed_input,
        stored_count=stored_count,
        pending_tool_calls=pending_tool_calls,
    )
    assert projection is not None

    def marker_sequence(items: list[JsonValue], *, roles: set[str]) -> list[str]:
        markers: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in (None, "message") or item.get("role") not in roles:
                continue
            content = item.get("content")
            assert isinstance(content, list)
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    markers.append(text)
        return markers

    expected_message_markers = marker_sequence(parsed_input, roles={"assistant", "developer", "user"})
    projected_message_markers = marker_sequence(
        projection.input_items,
        roles={"assistant", "developer", "user"},
    )
    assert projected_message_markers == expected_message_markers

    expected_call_ids = [
        item["call_id"]
        for item in parsed_input[:stored_count]
        if isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}
    ]
    projected_call_ids = [
        item["call_id"]
        for item in projection.input_items
        if isinstance(item, dict) and item.get("type") in {"function_call", "custom_tool_call"}
    ]
    projected_output_ids = [
        item["call_id"]
        for item in projection.input_items
        if isinstance(item, dict) and item.get("type") in {"function_call_output", "custom_tool_call_output"}
    ]
    assert projected_call_ids == expected_call_ids
    assert projected_output_ids == expected_call_ids
    assert len(projected_call_ids) == len(set(projected_call_ids))
    assert pending_tool_calls.keys().isdisjoint(projected_call_ids)
    assert all(not isinstance(item, dict) or item.get("type") != "reasoning" for item in projection.input_items)


def test_full_resend_exact_prefix_accepts_canonical_agent_message_before_user_retries() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    full_input: list[JsonValue] = [
        *stored_input,
        {
            "type": "reasoning",
            "id": "reasoning_previous",
            "encrypted_content": "opaque",
            "summary": [],
        },
        _canonical_agent_message(),
        {"type": "message", "role": "user", "content": "first retry"},
        {"type": "message", "role": "user", "content": "second retry"},
    ]
    classification = project_responses_input_for_account_neutral_fresh_replay(
        full_input,
        stored_count=len(stored_input),
        preserve_response_owned_agent_message_ids=True,
    )
    serialized = project_responses_input_for_account_neutral_fresh_replay(
        full_input,
        stored_count=len(stored_input),
    )

    assert classification is not None
    assert responses_input_suffix_retains_prior_output(
        classification.input_items,
        stored_count=classification.stored_prefix_count,
        exact_stored_prefix_without_pending_manifest=True,
    )
    assert serialized is not None
    assert not responses_input_suffix_retains_prior_output(
        serialized.input_items,
        stored_count=serialized.stored_prefix_count,
        exact_stored_prefix_without_pending_manifest=True,
    )
    assert not responses_payload_is_account_neutral_fresh_replay({"input": serialized.input_items})


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda item: item.pop("id"), id="missing-id"),
        pytest.param(lambda item: item.__setitem__("id", "msg_not_agent_owned"), id="wrong-id-prefix"),
        pytest.param(lambda item: item.__setitem__("id", "amsg_not-a-uuid"), id="invalid-id-uuid"),
        pytest.param(lambda item: item.__setitem__("author", "root/no-leading-slash"), id="invalid-author"),
        pytest.param(lambda item: item.__setitem__("author", "/foo"), id="non-root-author"),
        pytest.param(lambda item: item.__setitem__("author", "/root/.."), id="path-traversal-author"),
        pytest.param(lambda item: item.__setitem__("author", "/root/UPPER"), id="uppercase-author"),
        pytest.param(lambda item: item.__setitem__("recipient", "/root/with-hyphen"), id="invalid-recipient-segment"),
        pytest.param(lambda item: item.__setitem__("recipient", item["author"]), id="self-delivery"),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__("extra", True),
            id="extra-metadata",
        ),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__("turn_id", "bad-turn"),
            id="invalid-turn-id",
        ),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__("create_time", float("inf")),
            id="nonfinite-create-time",
        ),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__(
                "create_time",
                10**400,
            ),
            id="oversized-integer-create-time",
        ),
        pytest.param(
            lambda item: item.__setitem__("content", [{"type": "output_text", "text": "wrong direction"}]),
            id="output-content",
        ),
        pytest.param(lambda item: item.__setitem__("extra", "unbound"), id="extra-field"),
    ],
)
def test_full_resend_agent_message_proof_rejects_malformed_lookalikes(mutate) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    agent_message = _canonical_agent_message()
    mutate(agent_message)
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, agent_message, {"role": "user", "content": "follow-up"}],
        stored_count=len(stored_input),
        preserve_response_owned_agent_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        exact_stored_prefix_without_pending_manifest=True,
    )


def test_full_resend_agent_message_after_fresh_user_is_not_a_prior_output_boundary() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    projection = project_responses_input_for_account_neutral_fresh_replay(
        [
            *stored_input,
            {"role": "user", "content": "follow-up"},
            _canonical_agent_message(),
        ],
        stored_count=len(stored_input),
        preserve_response_owned_agent_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        exact_stored_prefix_without_pending_manifest=True,
    )


def test_full_resend_agent_message_proves_client_abandoned_undelivered_pending_call() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        _canonical_agent_message(),
    ]
    input_items: list[JsonValue] = [
        *stored_input,
        {
            "type": "reasoning",
            "id": "rs_old",
            "encrypted_content": "opaque",
            "summary": [],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
            },
        },
        _canonical_agent_message(),
        {"role": "user", "content": "first retry"},
        {"role": "user", "content": "second retry"},
    ]

    assert responses_input_suffix_proves_abandoned_pending_agent_boundary(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


def test_full_resend_real_codex_user_bookkeeping_is_stripped_after_boundary_proof() -> None:
    stored_input: list[JsonValue] = [
        _canonical_response_owned_user_message(
            message_id="msg_01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
            turn_id="01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
            text="first question",
        )
    ]
    first_retry = _canonical_response_owned_user_message(
        message_id="msg_01a02c31-2f60-7dd2-9f22-d7ef316596b1",
        turn_id="01a02c31-2f60-7dd2-9f22-d7ef316596b1",
        text="scheduled retry",
    )
    second_retry = _canonical_response_owned_user_message(text="continue")
    input_items: list[JsonValue] = [
        *stored_input,
        {
            "type": "reasoning",
            "id": "rs_08639659bba14680016a8a0902e9a887d090946ff27b898f05",
            "encrypted_content": "opaque",
            "summary": [],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
            },
        },
        _canonical_agent_message(),
        first_retry,
        second_retry,
    ]

    assert responses_input_suffix_proves_abandoned_pending_agent_boundary(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )
    projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=len(stored_input),
        omit_response_owned_agent_messages_from_stored_prefix=True,
    )
    assert projection is not None
    for item in projection.input_items:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        assert "id" not in item
        metadata = item.get("internal_chat_message_metadata_passthrough")
        assert isinstance(metadata, dict)
        assert set(metadata) == {"turn_id"}


def test_abandoned_pending_boundary_projects_response_owned_developer_in_exact_stored_prefix() -> None:
    developer_message = _canonical_response_owned_developer_message()
    stored_input: list[JsonValue] = [
        _canonical_response_owned_user_message(text="compacted user context"),
        developer_message,
        {
            "type": "custom_tool_call",
            "call_id": "call_settled",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_settled",
            "output": "/workspace",
        },
    ]
    input_items: list[JsonValue] = [
        *stored_input,
        {
            "type": "reasoning",
            "id": "rs_08639659bba14680016a8a0902e9a887d090946ff27b898f05",
            "encrypted_content": "opaque",
            "summary": [],
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
            },
        },
        _canonical_agent_message(),
        _canonical_response_owned_user_message(text="scheduled retry"),
        _canonical_response_owned_user_message(
            message_id="msg_01a02c59-7810-75fc-a7ee-ea7db5a66b6e",
            turn_id="01a02c59-7810-75fc-a7ee-ea7db5a66b6e",
            text="continue",
        ),
    ]
    pending_tool_calls = {"call_undelivered": "custom_tool_call"}

    assert responses_input_suffix_proves_abandoned_pending_agent_boundary(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    projection = project_responses_input_for_abandoned_pending_fresh_replay(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    assert projection is not None
    projected_developer = next(
        item
        for item in projection.input_items[: projection.stored_prefix_count]
        if isinstance(item, dict) and item.get("role") == "developer"
    )
    assert "id" not in projected_developer
    assert projected_developer["content"] == developer_message["content"]
    assert projected_developer["internal_chat_message_metadata_passthrough"] == {
        "turn_id": "01a02a78-d2b4-7a73-b03f-6d85db9cf496"
    }


def test_abandoned_pending_boundary_accepts_exact_http_transport_normalized_bookkeeping() -> None:
    """Codex strips internal metadata, but not response IDs, before HTTP."""

    stored_developer = _canonical_response_owned_developer_message()
    stored_developer.pop("internal_chat_message_metadata_passthrough")
    stored_user = _canonical_response_owned_user_message(text="stored user")
    stored_user.pop("internal_chat_message_metadata_passthrough")
    stored_input: list[JsonValue] = [
        {
            "type": "additional_tools",
            "role": "developer",
            "tools": [
                {
                    "type": "custom",
                    "name": "shell",
                    "description": "execute a bounded shell command",
                    "format": {"type": "text"},
                }
            ],
        },
        {
            "type": "message",
            "role": "developer",
            "content": [{"type": "input_text", "text": "request-scoped instructions"}],
        },
        stored_user,
        stored_developer,
        {
            "type": "custom_tool_call",
            "id": "ctc_transport_settled",
            "call_id": "call_transport_settled",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_transport_settled",
            "call_id": "call_transport_settled",
            "output": "/workspace",
        },
    ]
    reasoning = {
        "type": "reasoning",
        "id": "rs_08639659bba14680016a8a0902e9a887d090946ff27b898f05",
        "content": None,
        "encrypted_content": "opaque",
        "summary": [],
    }
    agent_message = _canonical_agent_message()
    agent_message.pop("internal_chat_message_metadata_passthrough")
    first_user = _canonical_response_owned_user_message(text="first retry")
    first_user.pop("internal_chat_message_metadata_passthrough")
    developer_followup = _canonical_response_owned_developer_message()
    developer_followup["id"] = "msg_01a02d3a-0319-76f1-9fd0-b28e9b9bc2d7"
    developer_followup.pop("internal_chat_message_metadata_passthrough")
    second_user = _canonical_response_owned_user_message(
        message_id="msg_01a02d3a-0321-7631-8a0a-7b90517b4bb2",
        turn_id="01a02d3a-0321-7631-8a0a-7b90517b4bb2",
        text="second retry",
    )
    second_user.pop("internal_chat_message_metadata_passthrough")
    input_items = [
        *stored_input,
        reasoning,
        agent_message,
        first_user,
        developer_followup,
        second_user,
    ]
    pending_tool_calls = {"call_undelivered": "custom_tool_call"}

    assert responses_input_suffix_proves_abandoned_pending_agent_boundary(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    parsed_reasoning = dict(reasoning)
    parsed_reasoning.pop("content")
    parsed_input_items = [*stored_input, parsed_reasoning, *input_items[len(stored_input) + 1 :]]
    assert responses_input_suffix_proves_abandoned_pending_agent_boundary(
        parsed_input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    projection = project_responses_input_for_abandoned_pending_fresh_replay(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    assert projection is not None
    assert all(
        not isinstance(item, dict) or item.get("role") not in {"developer", "user"} or "id" not in item
        for item in projection.input_items
    )
    assert all(not isinstance(item, dict) or item.get("type") != "reasoning" for item in projection.input_items)
    assert projection.input_items[-2] == {
        "type": "message",
        "role": "developer",
        "content": developer_followup["content"],
    }


def test_abandoned_pending_boundary_reports_first_content_free_rejection_branch() -> None:
    stored: list[JsonValue] = [{"role": "user", "content": "stored"}]
    boundary = _canonical_agent_message()
    followup: dict[str, JsonValue] = {"role": "user", "content": "follow-up"}
    pending = {"call_undelivered": "custom_tool_call"}

    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary, followup],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        is None
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary, followup],
            stored_count=0,
            pending_tool_calls=pending,
        )
        == "stored_prefix_invalid"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary, followup],
            stored_count=len(stored),
            pending_tool_calls={},
        )
        == "pending_call_manifest_missing"
    )
    malformed_reasoning: dict[str, JsonValue] = {
        "type": "reasoning",
        "id": "rs_fixture",
        "encrypted_content": "opaque",
        "summary": [],
        "extra": True,
    }
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, malformed_reasoning, boundary, followup],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        == "boundary_reasoning_shape_invalid"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, {"role": "assistant", "content": "lookalike"}, followup],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        == "boundary_agent_message_shape_invalid"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        == "followup_missing"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary, 7],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        == "followup_shape_invalid"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary, followup, {"type": "message", "role": "developer", "content": 7}, followup],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        == "developer_message_shape_invalid"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [*stored, boundary, followup, _canonical_response_owned_developer_message()],
            stored_count=len(stored),
            pending_tool_calls=pending,
        )
        == "developer_message_sequence_invalid"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [{"role": "user", "content": "stored", "call_id": "call_undelivered"}, boundary, followup],
            stored_count=1,
            pending_tool_calls=pending,
        )
        == "pending_call_conflict"
    )
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_orphan",
                    "output": "orphaned",
                },
                boundary,
                followup,
            ],
            stored_count=1,
            pending_tool_calls=pending,
        )
        == "projection_failed"
    )
    unmatched_call: dict[str, JsonValue] = {
        "type": "custom_tool_call",
        "call_id": "call_historical_unmatched",
        "name": "shell",
        "input": "pwd",
        "status": "completed",
    }
    assert (
        abandoned_pending_agent_boundary_rejection_reason(
            [unmatched_call, boundary, followup],
            stored_count=1,
            pending_tool_calls=pending,
        )
        == "direct_call_prefix_state_invalid"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda items: items[0].__setitem__("content", "not-null"), id="reasoning-content"),
        pytest.param(lambda items: items[1].__setitem__("extra", True), id="agent-extra-field"),
        pytest.param(lambda items: items[2].__setitem__("id", "msg_not-a-uuid"), id="user-invalid-id"),
        pytest.param(lambda items: items[3].__setitem__("extra", True), id="developer-extra-field"),
        pytest.param(
            lambda items: items[3].__setitem__("internal_chat_message_metadata_passthrough", {}),
            id="developer-empty-metadata",
        ),
    ],
)
def test_abandoned_pending_boundary_rejects_malformed_transport_normalized_bookkeeping(mutate) -> None:
    reasoning: dict[str, JsonValue] = {
        "type": "reasoning",
        "id": "rs_08639659bba14680016a8a0902e9a887d090946ff27b898f05",
        "content": None,
        "encrypted_content": "opaque",
        "summary": [],
    }
    agent_message = _canonical_agent_message()
    agent_message.pop("internal_chat_message_metadata_passthrough")
    user_message = _canonical_response_owned_user_message()
    user_message.pop("internal_chat_message_metadata_passthrough")
    developer_message = _canonical_response_owned_developer_message()
    developer_message.pop("internal_chat_message_metadata_passthrough")
    suffix = [reasoning, agent_message, user_message, developer_message]
    mutate(suffix)

    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        [{"role": "user", "content": "stored"}, *suffix],
        stored_count=1,
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


def test_abandoned_pending_boundary_requires_a_user_after_transport_developer_followup() -> None:
    reasoning: dict[str, JsonValue] = {
        "type": "reasoning",
        "id": "rs_08639659bba14680016a8a0902e9a887d090946ff27b898f05",
        "content": None,
        "encrypted_content": "opaque",
        "summary": [],
    }
    agent_message = _canonical_agent_message()
    agent_message.pop("internal_chat_message_metadata_passthrough")
    developer_message = _canonical_response_owned_developer_message()
    developer_message.pop("internal_chat_message_metadata_passthrough")

    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        [{"role": "user", "content": "stored"}, reasoning, agent_message, developer_message],
        stored_count=1,
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


def test_abandoned_pending_boundary_rejects_unbounded_transport_developer_followups() -> None:
    reasoning: dict[str, JsonValue] = {
        "type": "reasoning",
        "id": "rs_08639659bba14680016a8a0902e9a887d090946ff27b898f05",
        "content": None,
        "encrypted_content": "opaque",
        "summary": [],
    }
    agent_message = _canonical_agent_message()
    agent_message.pop("internal_chat_message_metadata_passthrough")
    first_user = _canonical_response_owned_user_message(text="first retry")
    first_user.pop("internal_chat_message_metadata_passthrough")
    second_user = _canonical_response_owned_user_message(
        message_id="msg_01a02d3a-0321-7631-8a0a-7b90517b4bb2",
        turn_id="01a02d3a-0321-7631-8a0a-7b90517b4bb2",
        text="second retry",
    )
    second_user.pop("internal_chat_message_metadata_passthrough")
    first_developer = _canonical_response_owned_developer_message()
    first_developer.pop("internal_chat_message_metadata_passthrough")
    second_developer = _canonical_response_owned_developer_message()
    second_developer["id"] = "msg_01a02d3a-0319-76f1-9fd0-b28e9b9bc2d7"
    second_developer.pop("internal_chat_message_metadata_passthrough")

    for followups in (
        [first_developer, first_user],
        [first_user, first_developer],
        [first_user, first_developer, second_developer, second_user],
    ):
        assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
            [{"role": "user", "content": "stored"}, reasoning, agent_message, *followups],
            stored_count=1,
            pending_tool_calls={"call_undelivered": "custom_tool_call"},
        )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda item: item.__setitem__("id", "msg_not-a-uuid"), id="invalid-message-id"),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__("extra", True),
            id="extra-metadata",
        ),
        pytest.param(
            lambda item: item.__setitem__("content", [{"type": "output_text", "text": "wrong authority"}]),
            id="output-content",
        ),
        pytest.param(lambda item: item.__setitem__("extra", "unbound"), id="extra-field"),
    ],
)
def test_abandoned_pending_boundary_rejects_malformed_response_owned_developer_messages(mutate) -> None:
    developer_message = _canonical_response_owned_developer_message()
    mutate(developer_message)
    stored_input: list[JsonValue] = [
        _canonical_response_owned_user_message(text="compacted user context"),
        developer_message,
    ]

    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        [*stored_input, _canonical_agent_message(), _canonical_response_owned_user_message()],
        stored_count=len(stored_input),
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


def test_abandoned_pending_boundary_drops_only_exact_leading_orphan_output() -> None:
    orphan_call_id = "call_clipped_before_retained_window"
    stored_input: list[JsonValue] = [
        {
            "type": "custom_tool_call_output",
            "call_id": orphan_call_id,
            "output": "historical result",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-clipped"},
        },
        {
            "type": "custom_tool_call",
            "id": "ctc_response_owned_retained",
            "call_id": "call_retained",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "turn-retained",
                "create_time": 1787433300.0,
            },
        },
        {
            "type": "custom_tool_call_output",
            "id": "ctco_response_owned_retained",
            "call_id": "call_retained",
            "output": "done",
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "turn-retained",
                "create_time": 1787433301.0,
            },
        },
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "historical task completed"}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-retained"},
        },
    ]
    input_items = [
        *stored_input,
        _canonical_agent_message(),
        _canonical_response_owned_user_message(text="continue"),
    ]
    pending_tool_calls = {"call_undelivered": "custom_tool_call"}

    assert responses_input_suffix_proves_abandoned_pending_agent_boundary(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    projection = project_responses_input_for_abandoned_pending_fresh_replay(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls=pending_tool_calls,
    )
    assert projection is not None
    assert projection.stored_prefix_count == len(stored_input) - 1
    projected_call = projection.input_items[0]
    projected_output = projection.input_items[1]
    assert isinstance(projected_call, dict)
    assert isinstance(projected_output, dict)
    assert projected_call["call_id"] == "call_retained"
    assert projected_call["internal_chat_message_metadata_passthrough"] == {"turn_id": "turn-retained"}
    assert projected_output["internal_chat_message_metadata_passthrough"] == {"turn_id": "turn-retained"}
    assert all(not isinstance(item, dict) or item.get("call_id") != orphan_call_id for item in projection.input_items)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda items: items[0].__setitem__("call_id", "call_undelivered"),
            id="orphan-is-abandoned-pending-call",
        ),
        pytest.param(
            lambda items: items.append(
                {
                    "type": "custom_tool_call",
                    "call_id": "call_clipped_before_retained_window",
                    "name": "shell",
                    "input": "pwd",
                    "status": "completed",
                }
            ),
            id="orphan-call-id-reused-later",
        ),
        pytest.param(
            lambda items: items.__setitem__(
                3,
                {"type": "message", "role": "user", "content": "no retained assistant boundary"},
            ),
            id="no-retained-assistant-boundary",
        ),
        pytest.param(
            lambda items: items.insert(
                2,
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_nonleading_orphan",
                    "output": "ambiguous",
                },
            ),
            id="nonleading-orphan-remains-invalid",
        ),
    ],
)
def test_abandoned_pending_boundary_rejects_ambiguous_orphan_output(mutate) -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "custom_tool_call_output",
            "call_id": "call_clipped_before_retained_window",
            "output": "historical result",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn-clipped"},
        },
        {
            "type": "custom_tool_call",
            "call_id": "call_retained",
            "name": "shell",
            "input": "pwd",
            "status": "completed",
        },
        {"type": "custom_tool_call_output", "call_id": "call_retained", "output": "done"},
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": "historical task completed",
        },
    ]
    mutate(stored_input)
    input_items = [*stored_input, _canonical_agent_message(), {"role": "user", "content": "continue"}]

    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        input_items,
        stored_count=len(stored_input),
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda item: item.__setitem__("id", "msg_not-a-uuid"), id="invalid-message-id"),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__("extra", True),
            id="extra-metadata",
        ),
        pytest.param(
            lambda item: item["internal_chat_message_metadata_passthrough"].__setitem__("create_time", -1),
            id="negative-create-time",
        ),
        pytest.param(lambda item: item.__setitem__("extra", "unbound"), id="extra-field"),
    ],
)
def test_abandoned_pending_boundary_rejects_malformed_response_owned_user_messages(mutate) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    user_message = _canonical_response_owned_user_message()
    mutate(user_message)

    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        [*stored_input, _canonical_agent_message(), user_message],
        stored_count=len(stored_input),
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param([_canonical_agent_message()], id="no-fresh-user"),
        pytest.param([{"role": "user", "content": "retry"}], id="no-agent-boundary"),
        pytest.param(
            [
                _canonical_agent_message(),
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_undelivered",
                    "output": "forged",
                },
            ],
            id="pending-output-present",
        ),
        pytest.param(
            [
                _canonical_agent_message(),
                {"role": "user", "content": "retry"},
                {
                    "type": "custom_tool_call",
                    "call_id": "call_other",
                    "name": "shell",
                    "input": "pwd",
                },
            ],
            id="new-call-after-user",
        ),
    ],
)
def test_abandoned_pending_agent_boundary_rejects_incomplete_or_tool_bearing_suffix(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(
            [
                {"type": "reasoning", "id": "rs_missing_encrypted_content", "summary": []},
                _canonical_agent_message(),
                {"role": "user", "content": "retry"},
            ],
            id="reasoning-missing-encrypted-content",
        ),
        pytest.param(
            [
                _canonical_agent_message(),
                {
                    "type": "reasoning",
                    "id": "rs_after_boundary",
                    "encrypted_content": "opaque",
                    "summary": [],
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": "01a02b31-bc02-70b0-a09e-0dedbc2e2da9",
                    },
                },
                {"role": "user", "content": "retry"},
            ],
            id="reasoning-after-agent-boundary",
        ),
    ],
)
def test_abandoned_pending_agent_boundary_rejects_unproven_or_misordered_reasoning(
    suffix: list[JsonValue],
) -> None:
    assert not responses_input_suffix_proves_abandoned_pending_agent_boundary(
        [{"role": "user", "content": "first question"}, *suffix],
        stored_count=1,
        pending_tool_calls={"call_undelivered": "custom_tool_call"},
    )


def test_full_resend_exact_settled_prefix_rejects_historical_call_id_reuse() -> None:
    input_items: list[JsonValue] = [
        {
            "type": "custom_tool_call",
            "call_id": "call_historical",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_historical",
            "output": "/workspace",
        },
        {
            "type": "custom_tool_call",
            "call_id": "call_historical",
            "name": "shell",
            "input": "git status --short",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_historical",
            "output": "",
        },
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"type": "message", "role": "user", "content": "next question"},
    ]

    assert not responses_input_suffix_retains_prior_output(
        input_items,
        stored_count=2,
        exact_stored_prefix_without_pending_manifest=True,
    )


def test_full_resend_retained_output_rejects_response_owned_fresh_developer() -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "id": "msg_answer",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {
            "type": "message",
            "id": "msg_user",
            "role": "user",
            "content": [{"type": "input_text", "text": "next question"}],
        },
        {
            "type": "message",
            "id": "msg_response_owned",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_current"},
            "content": [{"type": "input_text", "text": "new control message"}],
        },
    ]

    projection = project_responses_input_for_account_neutral_fresh_replay(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
        preserve_developer_message_ids=True,
    )

    assert projection is not None
    assert not responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
    )


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control without user"}],
                },
            ],
            id="developer-without-user",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control"}],
                },
                {"role": "user", "content": "later question"},
            ],
            id="developer-not-terminal",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "control without turn id"}],
                },
            ],
            id="developer-without-turn-id",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": "turn-current",
                        "account_id": "acc-1",
                    },
                    "content": [{"type": "input_text", "text": "account-bound control"}],
                },
            ],
            id="developer-with-account-metadata",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "type": 7,
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control with malformed type"}],
                },
            ],
            id="developer-with-nonstring-type",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "type": None,
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control with null type"}],
                },
            ],
            id="developer-with-null-type",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"type": "input_text", "text": "raw next input"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after raw input"}],
                },
            ],
            id="developer-after-raw-input-part",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"type": "input_text", "text": "raw next input"},
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after raw and user"}],
                },
            ],
            id="developer-after-raw-input-and-user",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "first next question"},
                {"role": "user", "content": "second next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after repeated users"}],
                },
            ],
            id="developer-after-repeated-user-messages",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "prior answer"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_file", "file_id": "file-account-bound"}],
                },
            ],
            id="developer-with-account-bound-file-id",
        ),
        pytest.param(
            [
                {
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "still working"}],
                },
                {"role": "user", "content": "next question"},
                {
                    "role": "developer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-current"},
                    "content": [{"type": "input_text", "text": "control after commentary"}],
                },
            ],
            id="developer-after-assistant-commentary",
        ),
    ],
)
def test_full_resend_retained_output_rejects_unproven_fresh_developer_followup(
    suffix: list[JsonValue],
) -> None:
    stored_input: list[JsonValue] = [{"role": "user", "content": "first question"}]

    assert (
        responses_input_suffix_retains_prior_output(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
        )
        is False
    )


def test_full_resend_retained_output_rejects_complete_tool_pair_between_output_and_user() -> None:
    stored_prefix: list[JsonValue] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "old"}],
        }
    ]
    full_input: list[JsonValue] = [
        *stored_prefix,
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "status": "completed",
            "content": [{"type": "output_text", "text": "done"}],
        },
        {
            "type": "custom_tool_call",
            "call_id": "call_extra",
            "name": "shell",
            "input": "pwd",
        },
        {
            "type": "custom_tool_call_output",
            "call_id": "call_extra",
            "output": "ok",
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "new"}],
        },
        {
            "type": "message",
            "role": "developer",
            "internal_chat_message_metadata_passthrough": {"turn_id": "turn_new"},
            "content": [{"type": "input_text", "text": "control"}],
        },
    ]
    projected_full_input = project_responses_input_for_account_neutral_fresh_replay(
        full_input,
        stored_count=len(stored_prefix),
    )

    assert projected_full_input is not None
    assert (
        responses_input_suffix_retains_prior_output(
            projected_full_input.input_items,
            stored_count=projected_full_input.stored_prefix_count,
        )
        is False
    )


def test_full_resend_retained_output_rejects_historical_developer_interleaving() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "custom_tool_call",
            "call_id": "call_old",
            "name": "shell",
            "input": "pwd",
        },
        {"role": "developer", "content": "historical control"},
        {
            "type": "custom_tool_call_output",
            "call_id": "call_old",
            "output": "/workspace",
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "prior answer"}],
        },
        {"role": "user", "content": "next question"},
    ]

    assert not responses_input_suffix_retains_prior_output(
        [*stored_input, *suffix],
        stored_count=len(stored_input),
    )


def test_full_resend_tool_loop_manifest_rejects_call_id_reused_from_stored_prefix() -> None:
    stored_input: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "old"},
    ]
    suffix: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup_again",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "new"},
    ]

    assert (
        responses_input_suffix_matches_pending_tool_calls(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
            pending_tool_calls={"call_1": "function_call"},
        )
        is False
    )


def test_full_resend_tool_loop_manifest_rejects_call_id_reused_from_unsupported_prefix_item() -> None:
    stored_input: list[JsonValue] = [
        {"role": "user", "content": "first question"},
        {
            "type": "computer_call",
            "call_id": "call_1",
            "action": {"type": "screenshot"},
            "status": "completed",
        },
    ]
    suffix: list[JsonValue] = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "new"},
    ]

    assert (
        responses_input_suffix_matches_pending_tool_calls(
            [*stored_input, *suffix],
            stored_count=len(stored_input),
            pending_tool_calls={"call_1": "function_call"},
        )
        is False
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"conversation": "conv_1", "input": []},
        {"previous_response_id": "resp_1", "input": []},
        {"prompt": {"id": "pmpt_1"}, "input": []},
        {"input": [{"type": "reasoning", "encrypted_content": "ciphertext"}]},
        {"input": [{"type": "reasoning", "encrypted_content": 123}]},
        {"input": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan"}]}]},
        {"input": [{"type": "input_file", "file_id": "file_1"}]},
        {"input": [{"type": "input_image", "image_url": "sediment://file_1"}]},
        {"input": [{"type": "input_file", "file_url": "sediment://file_1"}]},
        {"input": [{"type": "input_image", "image_url": "file:///tmp/account-a.png"}]},
        {"input": [{"type": "message", "file_ids": "file_1"}]},
        {"input": [{"type": "function_call_output", "call_id": "missing", "output": "orphan"}]},
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "duplicate",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "custom_tool_call",
                    "call_id": "duplicate",
                    "name": "shell",
                    "input": "pwd",
                },
                {"type": "function_call_output", "call_id": "duplicate", "output": "ambiguous"},
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_duplicate_output",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_duplicate_output",
                    "output": "first",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_duplicate_output",
                    "output": "duplicate",
                },
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1"},
                {"type": "function_call_output", "call_id": "call_1", "output": "underspecified"},
            ]
        },
        {"input": [{"type": "custom_tool_call", "call_id": "call_1", "name": "shell"}]},
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                    "caller": {"type": "program", "caller_id": "program_1"},
                },
                {"type": "function_call_output", "call_id": "call_1", "output": "ignored"},
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "ignored",
                    "caller": {"type": "program", "caller_id": "program_1"},
                },
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": [{"type": "input_file", "file_url": "sediment://file_account_a"}],
                },
            ]
        },
        {
            "input": [
                {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": [{"type": "input_image", "image_url": "sediment://file_account_a"}],
                },
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_in_progress",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_in_progress",
                    "output": "partial",
                    "status": "in_progress",
                },
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_mixed_patch",
                    "patch": "*** Begin Patch",
                    "input": "*** Begin Patch",
                }
            ]
        },
        {"input": [{"role": "user", "content": ""}]},
        {"input": [{"role": "user", "content": [{"type": "input_text", "text": "   "}]}]},
        {"input": [{"type": "apply_patch_call", "call_id": "call_1", "status": "completed"}]},
        {"input": [{"type": "reasoning", "id": "rs_1", "status": "completed"}]},
        {
            "input": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [{"type": "summary_text", "text": "stored reasoning"}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "stored output"}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                }
            ]
        },
        {"input": [{"type": "item_reference", "id": "msg_1"}]},
        {"input": [{"type": "file_search_call", "id": "search_1"}]},
        {"input": [{"type": "mcp_approval_request", "id": "approval_1"}]},
        {"input": [{"type": "future_hosted_state", "id": "future_1"}]},
        {"input": [{"role": "future", "content": "hello"}]},
        {"input": [{"type": "message", "content": "hello"}]},
        {"input": [{"content": "hello"}]},
        {
            "input": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "answer"},
                        {"type": "input_text", "text": "role-invalid"},
                    ],
                }
            ]
        },
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "output_text", "text": "role-invalid"}],
                }
            ]
        },
        {"input": [{"type": 1, "content": "hello"}]},
        {"input": [{"type": "input_text"}]},
        {"input": [{"type": "input_image"}]},
        {"input": [{"role": "user", "content": [{"type": "input_text"}]}]},
        {"input": ["malformed-list-item"]},
        {"input": [None]},
        {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "future_hosted_state", "id": "upstream_1"}],
                }
            ]
        },
        {"input": [{"role": "user", "content": "hello", "future_account_handle": "acct-A"}]},
        {
            "input": [
                {
                    "role": "user",
                    "content": "hello",
                    "internal_chat_message_metadata_passthrough": {"turn_id": 123},
                }
            ]
        },
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello", "future_account_handle": "acct-A"}],
                }
            ]
        },
        {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_future",
                    "name": "lookup",
                    "arguments": "{}",
                    "future_account_handle": "acct-A",
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_future",
                    "operation": {
                        "type": "update_file",
                        "path": "app.py",
                        "future_account_handle": "acct-A",
                    },
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_unknown",
                    "operation": {"type": "future_file_operation", "path": "app.py", "diff": "@@\n"},
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_malformed_create",
                    "operation": {"type": "create_file", "path": "new.txt"},
                }
            ]
        },
        {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_patch_malformed_delete",
                    "operation": {"type": "delete_file", "path": "old.txt", "diff": "unexpected"},
                }
            ]
        },
        {"input": [], "text": {"format": {"type": "stored_template", "template_id": "tmpl_account_a"}}},
        {"input": [], "client_metadata": {"future_account_handle": "acct-A"}},
        {"input": [], "container_id": "container_1"},
        {"input": [], "future_state": {"vector_store_id": "vs_1"}},
        {"input": [], "future": {"file_id": "file_1"}},
        {"input": [], "future": {"encrypted_content": "ciphertext"}},
        {"input": [], "reasoning": {"encrypted_content": "ciphertext"}},
        {"input": [], "reasoning": {"encrypted_content": 123}},
        {"input": [], "reasoning": {"future": {"file_id": "file_1"}}},
        {"input": [], "reasoning": {"effort": {"future": "high"}}},
        {"input": [], "agent_id": "agent_1"},
        {"input": [], "tools": [{"type": "file_search", "vector_store_ids": ["vs_1"]}]},
        {"input": [], "tools": [{}]},
        {"input": [], "tools": [{"type": "function", "name": "lookup", "container_id": "ctr_1"}]},
        {"input": [], "tools": [{"type": "web_search", "vector_store_ids": ["vs_1"]}]},
        {
            "input": [],
            "tools": [{"type": "web_search", "filters": {"future_account_handle": "acct-A"}}],
        },
        {
            "input": [],
            "tools": [{"type": "web_search", "filters": {"allowed_domains": "openai.com"}}],
        },
        {
            "input": [],
            "tools": [{"type": "web_search", "search_context_size": "unbounded"}],
        },
        {
            "input": [],
            "tools": [{"type": "web_search", "user_location": {"container_id": "ctr-A"}}],
        },
        {
            "input": [],
            "tools": [
                {
                    "type": "custom",
                    "name": "shell",
                    "format": {
                        "type": "grammar",
                        "parameters": {"container_id": "ctr_account_a"},
                    },
                }
            ],
        },
        {
            "input": [],
            "tool_choice": {"type": "file_search", "vector_store_id": "vs_account_a"},
        },
        {
            "input": [],
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "mcp", "server_label": "private"}],
            },
        },
        {
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [{"type": "mcp", "server_label": "private"}],
                }
            ]
        },
    ],
)
def test_account_neutral_fresh_replay_rejects_owner_scoped_or_unknown_state(
    payload: dict[str, JsonValue],
) -> None:
    assert responses_payload_is_account_neutral_fresh_replay(payload) is False


@pytest.mark.parametrize(
    "reasoning",
    [
        {"encrypted_content": "ciphertext"},
        {"future": {"file_id": "file_1"}},
    ],
)
def test_account_neutral_fresh_replay_rejects_reasoning_extras_preserved_by_request_model(
    reasoning: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "reasoning": reasoning,
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


@pytest.mark.parametrize(
    "unsafe_tool_state",
    [
        {"tool_choice": {"type": "file_search", "vector_store_id": "vs_account_a"}},
        {
            "tool_choice": {
                "type": "allowed_tools",
                "mode": "required",
                "tools": [{"type": "mcp", "server_label": "private"}],
            }
        },
        {"tools": [{"type": "function", "name": "lookup", "container_id": "ctr_account_a"}]},
        {"tools": [{"type": "web_search", "vector_store_ids": ["vs_account_a"]}]},
        {"tools": [{"type": "web_search", "filters": {"future_account_handle": "acct-A"}}]},
        {"tools": [{"type": "web_search", "search_context_size": "unbounded"}]},
        {"tools": [{"type": "web_search", "user_location": {"future_region_id": "region-A"}}]},
        {
            "tools": [
                {
                    "type": "custom",
                    "name": "shell",
                    "format": {
                        "type": "grammar",
                        "parameters": {"container_id": "ctr_account_a"},
                    },
                }
            ]
        },
    ],
)
def test_account_neutral_fresh_replay_rejects_tool_state_preserved_by_request_model(
    unsafe_tool_state: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            **unsafe_tool_state,
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


@pytest.mark.parametrize(
    "unsafe_state",
    [
        {"text": {"format": {"type": "stored_template", "template_id": "tmpl_account_a"}}},
        {"client_metadata": {"future_account_handle": "acct-A"}},
    ],
)
def test_account_neutral_fresh_replay_rejects_unknown_controls_preserved_by_request_model(
    unsafe_state: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            **unsafe_state,
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


@pytest.mark.parametrize(
    "input_item",
    [
        {"role": "user", "content": "hello", "future_account_handle": "acct-A"},
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "hello", "future_account_handle": "acct-A"}],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": "{}",
            "future_account_handle": "acct-A",
        },
        {
            "type": "apply_patch_call",
            "call_id": "call_1",
            "patch": "*** Begin Patch",
            "operation": {
                "type": "future",
                "path": "app.py",
                "future_account_handle": "acct-A",
            },
        },
    ],
)
def test_account_neutral_fresh_replay_rejects_nested_extras_preserved_by_request_model(
    input_item: dict[str, JsonValue],
) -> None:
    request = ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [input_item],
        }
    )

    assert responses_payload_is_account_neutral_fresh_replay(request.to_payload()) is False


def test_account_neutral_replay_marker_requires_tagged_existing_hard_kind() -> None:
    kind, key = make_http_bridge_account_neutral_replay_key("nonce")

    assert is_http_bridge_account_neutral_replay(kind=kind, key=key) is True
    assert (
        is_http_bridge_account_neutral_replay(
            kind=kind,
            key="account-neutral-replay:v1:",
        )
        is False
    )
    assert is_http_bridge_account_neutral_replay(kind=kind, key="a" * 64) is False
    assert is_http_bridge_account_neutral_replay(kind="turn_state_header", key=key) is False


def test_account_neutral_replay_marker_rejects_empty_nonce() -> None:
    with pytest.raises(ValueError, match="nonce"):
        make_http_bridge_account_neutral_replay_key("")
