"""Shared normalization for output items exposed by the public Responses API."""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping

_PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES = frozenset(
    {
        "message",
        "compaction",
        "function_call",
        "function_call_output",
        "reasoning",
        "web_search_call",
        "file_search_call",
        "computer_call",
        "code_interpreter_call",
        "mcp_approval_request",
        "mcp_list_tools",
        "output_image",
    }
)
PUBLIC_RESPONSE_TEXT_PART_TYPES = frozenset({"output_text", "input_text", "text", "refusal"})
_REASONING_SUMMARY_BLANK_HTML_COMMENT_RE = re.compile(r"(?m)^[ \t]*<!--\s*-->[ \t]*(?:\r?\n|\Z)")


def strip_blank_html_comment_lines(text: str) -> str:
    terminal_match = None
    for match in _REASONING_SUMMARY_BLANK_HTML_COMMENT_RE.finditer(text):
        if match.end() == len(text):
            terminal_match = match
    cleaned, count = _REASONING_SUMMARY_BLANK_HTML_COMMENT_RE.subn("", text)
    if count == 0:
        return text
    if terminal_match is not None:
        return cleaned.rstrip("\r\n")
    return cleaned


def normalize_public_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    """Return the exact item representation exposed on the public API."""

    item_type = item.get("type")
    if item_type == "reasoning":
        return _normalize_reasoning_output_item(item)
    if isinstance(item_type, str) and is_public_passthrough_output_item_type(item_type):
        return dict(item)
    text_value = extract_public_output_item_text(item)
    if text_value is None:
        return None
    normalized: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "status": item.get("status") if isinstance(item.get("status"), str) else "completed",
        "content": [{"type": "output_text", "text": text_value}],
    }
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        normalized["id"] = item_id
    return normalized


def extract_public_output_item_text(item: Mapping[str, JsonValue]) -> str | None:
    direct_text = item.get("text")
    if isinstance(direct_text, str) and direct_text:
        return direct_text
    content = item.get("content")
    if is_json_mapping(content):
        content_parts: list[Mapping[str, JsonValue]] = [content]
    elif isinstance(content, list):
        content_parts = [part for part in content if is_json_mapping(part)]
    else:
        content_parts = []
    parts: list[str] = []
    for part in content_parts:
        part_type = part.get("type")
        if isinstance(part_type, str) and part_type in PUBLIC_RESPONSE_TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
                continue
        text = part.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    if parts:
        return "".join(parts)
    summary = item.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return None


def is_public_passthrough_output_item_type(item_type: str) -> bool:
    return (
        item_type in _PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES
        or item_type.endswith("_call")
        or item_type.endswith("_call_output")
    )


def _normalize_reasoning_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    normalized = dict(item)
    summary = item.get("summary")
    if not isinstance(summary, list):
        return normalized

    normalized_summary: list[JsonValue] = []
    changed = False
    for part in summary:
        if not is_json_mapping(part):
            normalized_summary.append(part)
            continue
        text = part.get("text")
        if part.get("type") != "summary_text" or not isinstance(text, str):
            normalized_summary.append(dict(part))
            continue
        cleaned = strip_blank_html_comment_lines(text)
        normalized_part = dict(part)
        normalized_part["text"] = cleaned
        normalized_summary.append(normalized_part)
        changed = changed or cleaned != text

    if changed:
        normalized["summary"] = normalized_summary
    return normalized
