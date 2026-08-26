from __future__ import annotations

import copy

from app.core.openai.public_output import (
    MAX_PUBLIC_RESPONSE_OUTPUT_ITEMS,
    collect_public_output_item_event,
    merge_public_response_output_items,
)
from app.core.types import JsonValue
from app.modules.proxy.response_transition_manifest import (
    build_response_transition_manifest,
    decode_response_transition_manifest,
    encode_response_transition_manifest,
    match_response_transition_manifest_prefix,
    response_transition_manifest_matches_context,
)


def _completed_payload() -> dict[str, JsonValue]:
    return {
        "type": "response.completed",
        "response": {
            "id": "resp_manifest_1",
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "rs_manifest_1",
                    "encrypted_content": "secret-reasoning-ciphertext",
                    "summary": [],
                    "status": "completed",
                },
                {
                    "type": "message",
                    "id": "msg_manifest_1",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "secret commentary"}],
                },
                {
                    "type": "custom_tool_call",
                    "id": "ctc_manifest_1",
                    "call_id": "call_manifest_1",
                    "name": "shell",
                    "input": "secret command",
                    "status": "completed",
                },
            ],
        },
    }


def test_streamed_output_item_collection_is_bounded_and_ordered() -> None:
    output_items: dict[int, dict[str, JsonValue]] = {}
    second_item: dict[str, JsonValue] = {"type": "message", "role": "assistant", "content": []}
    first_item: dict[str, JsonValue] = {"type": "reasoning", "summary": []}

    assert collect_public_output_item_event(
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": second_item,
        },
        output_items,
    )
    assert collect_public_output_item_event(
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": first_item,
        },
        output_items,
    )
    assert merge_public_response_output_items(
        {"id": "resp_streamed", "status": "completed", "output": []},
        output_items,
    )["output"] == [first_item, second_item]

    assert not collect_public_output_item_event(
        {
            "type": "response.output_item.done",
            "output_index": MAX_PUBLIC_RESPONSE_OUTPUT_ITEMS,
            "item": first_item,
        },
        output_items,
    )
    assert not collect_public_output_item_event(
        {
            "type": "response.output_item.done",
            "output_index": True,
            "item": first_item,
        },
        output_items,
    )


def test_response_transition_manifest_round_trip_is_content_free() -> None:
    manifest = build_response_transition_manifest(
        _completed_payload(),
        pending_tool_calls={"call_manifest_1": "custom_tool_call"},
    )

    assert manifest is not None
    encoded = encode_response_transition_manifest(manifest)
    assert encoded is not None
    assert "secret" not in encoded
    assert "call_manifest_1" not in encoded
    assert "resp_manifest_1" not in encoded
    assert decode_response_transition_manifest(encoded) == manifest
    assert response_transition_manifest_matches_context(
        manifest,
        response_id="resp_manifest_1",
        pending_tool_calls={"call_manifest_1": "custom_tool_call"},
    )
    assert not response_transition_manifest_matches_context(
        manifest,
        response_id="resp_other",
        pending_tool_calls={"call_manifest_1": "custom_tool_call"},
    )
    assert decode_response_transition_manifest(encoded.replace('"kind":"reasoning"', '"kind":"unknown"')) is None


def test_response_transition_manifest_matches_only_exact_ordered_output_prefix() -> None:
    payload = _completed_payload()
    manifest = build_response_transition_manifest(
        payload,
        pending_tool_calls={"call_manifest_1": "custom_tool_call"},
    )
    assert manifest is not None
    response = payload["response"]
    assert isinstance(response, dict)
    output = response["output"]
    assert isinstance(output, list)
    input_items: list[JsonValue] = [
        {"type": "message", "role": "user", "content": "stored"},
        *copy.deepcopy(output),
    ]

    assert match_response_transition_manifest_prefix(input_items, stored_count=1, manifest=manifest) == 4

    reordered = copy.deepcopy(input_items)
    reordered[1], reordered[2] = reordered[2], reordered[1]
    assert match_response_transition_manifest_prefix(reordered, stored_count=1, manifest=manifest) is None

    mutated = copy.deepcopy(input_items)
    assert isinstance(mutated[2], dict)
    mutated[2]["content"] = [{"type": "output_text", "text": "changed"}]
    assert match_response_transition_manifest_prefix(mutated, stored_count=1, manifest=manifest) is None


def test_response_transition_manifest_rejects_inconsistent_or_unsupported_output() -> None:
    payload = _completed_payload()

    assert (
        build_response_transition_manifest(
            payload,
            pending_tool_calls={"call_other": "custom_tool_call"},
        )
        is None
    )

    unsupported = copy.deepcopy(payload)
    response = unsupported["response"]
    assert isinstance(response, dict)
    output = response["output"]
    assert isinstance(output, list)
    assert isinstance(output[0], dict)
    output[0]["type"] = "computer_call"
    assert (
        build_response_transition_manifest(
            unsupported,
            pending_tool_calls={"call_manifest_1": "custom_tool_call"},
        )
        is None
    )


def test_response_transition_manifest_uses_public_retry_representation() -> None:
    payload: dict[str, JsonValue] = {
        "type": "response.completed",
        "response": {
            "id": "resp_manifest_public_1",
            "status": "completed",
            "output": [
                {
                    "type": "agent_message",
                    "id": "amsg_01a02b33-3b30-7742-bdb3-091f07cf2ea0",
                    "author": "/root/worker",
                    "recipient": "/root",
                    "content": [{"type": "input_text", "text": "completed result"}],
                }
            ],
        },
    }
    manifest = build_response_transition_manifest(
        payload,
        pending_tool_calls={},
        normalize_for_public_contract=True,
    )

    assert manifest is not None
    assert manifest.item_kinds == ("message:assistant",)
    assert (
        match_response_transition_manifest_prefix(
            [
                {"role": "user", "content": "stored"},
                {
                    "type": "message",
                    "id": "amsg_01a02b33-3b30-7742-bdb3-091f07cf2ea0",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "completed result"}],
                },
            ],
            stored_count=1,
            manifest=manifest,
        )
        == 2
    )
