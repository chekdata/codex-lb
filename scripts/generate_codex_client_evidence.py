"""Generate a content-free, fail-closed receipt from one Codex session JSONL.

This tool owns only the independently derived client evidence domain. It never
accepts or copies server challenge fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "qk_codex_client_evidence_v2"
LEDGER_SCHEMA = "qk_client_full_checkpoint_tool_ledger_v1"
LEDGER_DOMAIN = b"qk-client-full-checkpoint-tool-ledger-v1\0"
ID_DOMAIN = b"qk-client-full-checkpoint-tool-id-v1\0"
IDENTITY_DOMAIN = b"qk-client-full-checkpoint-tool-identity-v1\0"
ARGUMENTS_DOMAIN = b"qk-client-full-checkpoint-tool-arguments-v1\0"
OUTPUT_DOMAIN = b"qk-client-full-checkpoint-tool-output-v1\0"
PAYLOAD_DOMAIN = b"qk-client-full-checkpoint-tool-payload-v1\0"
AUTHORITY_DOMAIN = b"qk-http-bridge-task-authority-v1\0"
ERROR_CODE_DOMAIN = b"qk-client-terminal-error-code-v1\0"
ERROR_MESSAGE_DOMAIN = b"qk-client-terminal-error-message-v1\0"
_VIOLATIONS = (
    "pending_calls",
    "missing_id_events",
    "orphan_outputs",
    "duplicate_call_ids",
    "duplicate_outputs",
    "type_mismatches",
)


class EvidenceError(Exception):
    """A stable, content-free fail-closed error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _domain_hash(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + _canonical(value)).hexdigest()


def _read_once(path: Path) -> Snapshot:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError("jsonl_open_failed") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("jsonl_not_regular_file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise EvidenceError("jsonl_changed_during_read")
    data = b"".join(chunks)
    if len(data) != after.st_size:
        raise EvidenceError("jsonl_short_read")
    return Snapshot(
        data,
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        hashlib.sha256(data).hexdigest(),
    )


def stable_snapshot(path: Path, delay_seconds: float) -> Snapshot:
    first = _read_once(path)
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    second = _read_once(path)
    if (
        first.device,
        first.inode,
        first.size,
        first.mtime_ns,
        first.sha256,
    ) != (second.device, second.inode, second.size, second.mtime_ns, second.sha256):
        raise EvidenceError("jsonl_not_stable_across_reads")
    return second


def _event_kind(item_type: object) -> str | None:
    if not isinstance(item_type, str):
        return None
    if item_type.endswith("_call_output"):
        return "output"
    if item_type.endswith("_call"):
        return "call"
    return None


def _correlation_id(payload: dict[str, Any]) -> str | None:
    for key in ("call_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _task_authority_digest(identity: str) -> str:
    payload = bytearray(AUTHORITY_DOMAIN)
    for tag in ("session-id", "prompt_cache_key", "thread-id"):
        tag_bytes = tag.encode()
        value_bytes = identity.strip().encode()
        payload.extend(len(tag_bytes).to_bytes(2, "big"))
        payload.extend(tag_bytes)
        payload.extend(len(value_bytes).to_bytes(4, "big"))
        payload.extend(value_bytes)
    return hashlib.sha256(payload).hexdigest()


def _terminal_error_summary(payload: dict[str, Any]) -> dict[str, object]:
    error = payload.get("error")
    if error is None:
        return {"present": False, "class": "none", "code_digest": None, "message_digest": None}
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        return {
            "present": True,
            "class": "object",
            "code_digest": _domain_hash(ERROR_CODE_DOMAIN, code) if code is not None else None,
            "message_digest": _domain_hash(ERROR_MESSAGE_DOMAIN, message) if message is not None else None,
        }
    return {
        "present": True,
        "class": type(error).__name__,
        "code_digest": None,
        "message_digest": _domain_hash(ERROR_MESSAGE_DOMAIN, error),
    }


def generate_evidence(snapshot: Snapshot, expected_task_id: str) -> dict[str, object]:
    if not snapshot.data or not snapshot.data.endswith(b"\n"):
        raise EvidenceError("jsonl_missing_complete_newline")
    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(snapshot.data.splitlines(), 1):
        if not line:
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("jsonl_parse_failed") from exc
        if not isinstance(record, dict):
            raise EvidenceError("jsonl_record_not_object")
        records.append((line_number, record))

    session_meta = [
        record["payload"]
        for _, record in records
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict)
    ]
    if len(session_meta) != 1 or session_meta[0].get("id") != expected_task_id:
        raise EvidenceError("session_meta_task_mismatch")
    if isinstance(session_meta[0].get("source"), dict):
        raise EvidenceError("non_root_session_source_unsupported")

    # The exact supported client emits root session_id/thread_id equal to the
    # session_meta id. Require every persisted occurrence to agree.
    identity_values: dict[str, list[str]] = {"session_id": [], "thread_id": []}

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in identity_values and isinstance(child, str):
                    identity_values[key].append(child)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for _, record in records:
        collect(record)
    if any(not values or any(value != expected_task_id for value in values) for values in identity_values.values()):
        raise EvidenceError("root_transport_identity_not_derivable")

    entries: list[dict[str, object]] = []
    pending: dict[str, str] = {}
    seen_calls: set[str] = set()
    seen_outputs: set[str] = set()
    violations: Counter[str] = Counter()
    last_started_line: int | None = None
    last_complete_line: int | None = None
    last_complete_payload: dict[str, Any] | None = None
    latest_turn_counts: Counter[str] = Counter()

    for line_number, record in records:
        payload_value = record.get("payload")
        payload: dict[str, Any] = payload_value if isinstance(payload_value, dict) else {}
        item_type = payload.get("type")
        if item_type == "task_started":
            last_started_line = line_number
            latest_turn_counts.clear()
        elif item_type == "task_complete":
            last_complete_line = line_number
            last_complete_payload = payload
        elif last_started_line is not None and (last_complete_line is None or line_number > last_complete_line):
            if record.get("type") == "response_item":
                if item_type in {"message", "agent_message"} and payload.get("role") != "user":
                    latest_turn_counts["assistant_outputs"] += 1
                kind = _event_kind(item_type)
                if kind == "call":
                    latest_turn_counts["tool_calls"] += 1
                elif kind == "output":
                    latest_turn_counts["tool_outputs"] += 1

        if record.get("type") != "response_item":
            continue
        kind = _event_kind(item_type)
        if kind is None:
            continue
        raw_id = _correlation_id(payload)
        id_digest = _domain_hash(ID_DOMAIN, raw_id) if raw_id is not None else None
        identity = {
            "type": item_type,
            "name": payload.get("name") if isinstance(payload.get("name"), str) else None,
            "namespace": payload.get("namespace") if isinstance(payload.get("namespace"), str) else None,
        }
        if kind == "call":
            arguments = (
                {"source": "arguments", "value": payload["arguments"]}
                if "arguments" in payload
                else {"source": "input", "value": payload["input"]}
                if "input" in payload
                else {"source": "missing"}
            )
            output: object = {"source": "not_applicable"}
        else:
            arguments = {"source": "not_applicable"}
            output = {"source": "output", "value": payload["output"]} if "output" in payload else {"source": "missing"}
        entries.append(
            {
                "ordinal": len(entries) + 1,
                "jsonl_line": line_number,
                "kind": kind,
                "item_type": item_type,
                "id_digest": id_digest,
                "tool_identity_digest": _domain_hash(IDENTITY_DOMAIN, identity),
                "arguments_digest": _domain_hash(ARGUMENTS_DOMAIN, arguments),
                "output_digest": _domain_hash(OUTPUT_DOMAIN, output),
                "payload_without_id_digest": _domain_hash(
                    PAYLOAD_DOMAIN, {key: value for key, value in payload.items() if key not in {"call_id", "id"}}
                ),
            }
        )
        if raw_id is None:
            violations["missing_id_events"] += 1
        elif kind == "call":
            if raw_id in seen_calls:
                violations["duplicate_call_ids"] += 1
            else:
                seen_calls.add(raw_id)
                pending[raw_id] = str(item_type)
        elif raw_id in seen_outputs:
            violations["duplicate_outputs"] += 1
        else:
            seen_outputs.add(raw_id)
            call_type = pending.pop(raw_id, None)
            if call_type is None:
                violations["orphan_outputs"] += 1
            elif f"{call_type}_output" != item_type:
                violations["type_mismatches"] += 1

    violations["pending_calls"] = len(pending)
    unresolved_count = sum(violations[key] for key in _VIOLATIONS)
    if unresolved_count:
        raise EvidenceError("tool_ledger_unresolved")
    if last_started_line is None or last_complete_line is None or last_complete_line <= last_started_line:
        raise EvidenceError("latest_turn_not_terminal")
    post_terminal = [
        (record.get("type"), (record.get("payload") or {}).get("type"))
        for line_number, record in records
        if line_number > last_complete_line
    ]
    if any(item != ("event_msg", "item_completed") for item in post_terminal):
        raise EvidenceError("unsupported_post_terminal_event")

    ledger_payload = {"schema": LEDGER_SCHEMA, "entries": entries}
    task_authority_digest = _task_authority_digest(expected_task_id)
    strong_session_hash = hashlib.sha256(
        _canonical({"kind": "task_authority", "value": task_authority_digest})
    ).hexdigest()
    terminal_error = _terminal_error_summary(last_complete_payload or {})
    evidence: dict[str, object] = {
        "schema": SCHEMA,
        "content_free": True,
        "server_challenge_fields_included": False,
        "remote_session_jsonl_sha256": snapshot.sha256,
        "remote_session_jsonl_size_bytes": snapshot.size,
        "remote_session_jsonl_last_offset": snapshot.size,
        "remote_session_jsonl_line_count": len(records),
        "task_identity": expected_task_id,
        "session_identity": expected_task_id,
        "task_authority_digest": task_authority_digest,
        "strong_session_hash": strong_session_hash,
        "full_checkpoint_tool_ledger_digest": hashlib.sha256(LEDGER_DOMAIN + _canonical(ledger_payload)).hexdigest(),
        "full_checkpoint_tool_ledger_event_count": len(entries),
        "unresolved_count": unresolved_count,
        "ledger_validation": {key: violations[key] for key in _VIOLATIONS},
        "terminal": {
            "last_task_started_line": last_started_line,
            "last_task_complete_line": last_complete_line,
            "post_terminal_item_completed_count": len(post_terminal),
            "error_terminal": terminal_error["present"],
            "error": terminal_error,
            "latest_turn_assistant_output_count": latest_turn_counts["assistant_outputs"],
            "latest_turn_tool_call_count": latest_turn_counts["tool_calls"],
            "latest_turn_tool_output_count": latest_turn_counts["tool_outputs"],
        },
        "transport_identity": {
            "session_header": "session-id",
            "thread_header": "thread-id",
            "prompt_cache_key_source": "session_id_default",
        },
    }
    return evidence


def _create_new(path: Path, payload: bytes) -> None:
    path.parent.resolve(strict=True)
    if path.exists():
        raise EvidenceError("output_exists")
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        written = 0
        while written < len(payload):
            count = os.write(fd, payload[written:])
            if count <= 0:
                raise EvidenceError("output_short_write")
            written += count
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temp, path)
    except FileExistsError as exc:
        raise EvidenceError("output_exists") from exc
    finally:
        temp.unlink(missing_ok=True)
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--stability-delay-ms", type=int, default=250)
    args = parser.parse_args(argv)
    try:
        if args.stability_delay_ms < 0:
            raise EvidenceError("invalid_stability_delay")
        evidence = generate_evidence(stable_snapshot(args.jsonl, args.stability_delay_ms / 1000), args.task_id)
        encoded = _canonical(evidence) + b"\n"
        _create_new(args.output, encoded)
        print(f"evidence_path={args.output}")
        print(f"evidence_sha256={hashlib.sha256(encoded).hexdigest()}")
        return 0
    except EvidenceError as exc:
        print(f"ERROR {exc.code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
