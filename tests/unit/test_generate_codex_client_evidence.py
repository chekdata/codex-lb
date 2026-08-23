from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_generator() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts" / "generate_codex_client_evidence.py"
    spec = importlib.util.spec_from_file_location("generate_codex_client_evidence", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = _load_generator()
TASK_ID = "01a00000-0000-7000-8000-000000000001"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "codex_client_evidence" / "sanitized_terminal.jsonl"


def _record(record_type: str, payload_type: str, **payload: object) -> dict[str, Any]:
    return {"type": record_type, "payload": {"type": payload_type, **payload}}


def _valid_records(*, terminal_error: object = None) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    records[-2]["payload"]["error"] = terminal_error
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]], *, newline: bool = True) -> bytes:
    data = b"\n".join(json.dumps(record, separators=(",", ":")).encode() for record in records)
    if newline:
        data += b"\n"
    path.write_bytes(data)
    return data


def _snapshot(path: Path):
    return generator._read_once(path)


def test_generates_content_free_client_domain_only(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    raw = _write_jsonl(jsonl, _valid_records())

    evidence = generator.generate_evidence(_snapshot(jsonl), TASK_ID)
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))

    assert evidence["schema"] == "qk_codex_client_evidence_v2"
    assert evidence["content_free"] is True
    assert evidence["server_challenge_fields_included"] is False
    assert evidence["remote_session_jsonl_size_bytes"] == len(raw)
    assert evidence["remote_session_jsonl_last_offset"] == len(raw)
    assert evidence["full_checkpoint_tool_ledger_event_count"] == 2
    assert evidence["unresolved_count"] == 0
    assert evidence["terminal"] == {
        "last_task_started_line": 3,
        "last_task_complete_line": 8,
        "post_terminal_item_completed_count": 1,
        "error_terminal": False,
        "error": {"present": False, "class": "none", "code_digest": None, "message_digest": None},
        "latest_turn_assistant_output_count": 1,
        "latest_turn_tool_call_count": 1,
        "latest_turn_tool_output_count": 1,
    }
    for secret in (
        "SANITIZED_USER_TEXT",
        "SANITIZED_ASSISTANT_TEXT",
        "RAW_CALL_ID_MUST_NOT_ESCAPE",
        "RAW_ARGUMENT_MUST_NOT_ESCAPE",
        "RAW_OUTPUT_MUST_NOT_ESCAPE",
    ):
        assert secret not in encoded
    for server_field in (
        "captured_input_item_count",
        "captured_input_fingerprint",
        "non_input_contract_fingerprint",
        "retained_request_direct_call_ledger_digest",
        "captured_projected_payload_fingerprint",
        "captured_actual_wire_fingerprint",
        "captured_request_binding_provenance",
    ):
        assert server_field not in evidence


def test_cli_create_new_mode_600_and_refuses_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    jsonl = tmp_path / "session.jsonl"
    output = tmp_path / "receipt.json"
    _write_jsonl(jsonl, _valid_records())

    arguments = [
        "--jsonl",
        str(jsonl),
        "--task-id",
        TASK_ID,
        "--output",
        str(output),
        "--stability-delay-ms",
        "0",
    ]
    assert generator.main(arguments) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    stdout = capsys.readouterr().out
    assert f"evidence_path={output}" in stdout
    assert "evidence_sha256=" in stdout

    assert generator.main(arguments) == 2
    assert "ERROR output_exists" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda records: records[:-1], None),
        (lambda records: [*records[:-2], records[-1]], "latest_turn_not_terminal"),
        (
            lambda records: [
                record for record in records if record.get("payload", {}).get("type") != "custom_tool_call_output"
            ],
            "tool_ledger_unresolved",
        ),
        (
            lambda records: [
                *records[:-2],
                _record(
                    "response_item",
                    "custom_tool_call_output",
                    call_id="ORPHAN_CALL_ID",
                    output="SANITIZED",
                ),
                *records[-2:],
            ],
            "tool_ledger_unresolved",
        ),
        (
            lambda records: [*records[:6], records[5], *records[6:]],
            "tool_ledger_unresolved",
        ),
        (
            lambda records: [*records[:7], records[6], *records[7:]],
            "tool_ledger_unresolved",
        ),
        (
            lambda records: [
                *records[:6],
                _record(
                    "response_item",
                    "function_call_output",
                    call_id="RAW_CALL_ID_MUST_NOT_ESCAPE",
                    output="SANITIZED",
                ),
                *records[7:],
            ],
            "tool_ledger_unresolved",
        ),
        (
            lambda records: [*records, _record("response_item", "message", role="assistant", content="LATE")],
            "unsupported_post_terminal_event",
        ),
    ],
    ids=(
        "no_post_terminal_event",
        "missing_task_complete",
        "pending_call",
        "orphan_output",
        "duplicate_call_id",
        "duplicate_output",
        "call_output_type_mismatch",
        "unsupported_event_after_terminal",
    ),
)
def test_fail_closed_structural_cases(
    tmp_path: Path,
    mutate,
    expected: str | None,
) -> None:
    records = mutate(_valid_records())
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, records)
    if expected is None:
        # Removing only the permitted post-terminal event remains a valid terminal prefix.
        assert generator.generate_evidence(_snapshot(jsonl), TASK_ID)["unresolved_count"] == 0
    else:
        with pytest.raises(generator.EvidenceError, match=expected):
            generator.generate_evidence(_snapshot(jsonl), TASK_ID)


def test_refuses_incomplete_newline_and_wrong_task(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, _valid_records(), newline=False)
    with pytest.raises(generator.EvidenceError, match="jsonl_missing_complete_newline"):
        generator.generate_evidence(_snapshot(jsonl), TASK_ID)

    _write_jsonl(jsonl, _valid_records())
    with pytest.raises(generator.EvidenceError, match="session_meta_task_mismatch"):
        generator.generate_evidence(_snapshot(jsonl), "01a00000-0000-7000-8000-000000000099")


def test_refuses_subagent_source_and_mixed_transport_identity(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    records = _valid_records()
    records[0]["payload"]["source"] = {"subagent": "replacement"}
    _write_jsonl(jsonl, records)
    with pytest.raises(generator.EvidenceError, match="non_root_session_source_unsupported"):
        generator.generate_evidence(_snapshot(jsonl), TASK_ID)

    records = _valid_records()
    records[1]["payload"]["thread_id"] = "different-thread"
    _write_jsonl(jsonl, records)
    with pytest.raises(generator.EvidenceError, match="root_transport_identity_not_derivable"):
        generator.generate_evidence(_snapshot(jsonl), TASK_ID)


def test_terminal_error_is_hashed_without_copying_text(tmp_path: Path) -> None:
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(
        jsonl,
        _valid_records(terminal_error={"code": "PRIVATE_CODE", "message": "PRIVATE_MESSAGE"}),
    )
    evidence = generator.generate_evidence(_snapshot(jsonl), TASK_ID)
    encoded = json.dumps(evidence)
    error = evidence["terminal"]["error"]  # type: ignore[index]
    assert error["present"] is True
    assert evidence["terminal"]["error_terminal"] is True  # type: ignore[index]
    assert error["class"] == "object"
    assert error["code_digest"]
    assert error["message_digest"]
    assert "PRIVATE_CODE" not in encoded
    assert "PRIVATE_MESSAGE" not in encoded


def test_stable_snapshot_refuses_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(jsonl, _valid_records())
    first = _snapshot(jsonl)
    second = generator.Snapshot(
        first.data + b"x",
        first.device,
        first.inode,
        first.size + 1,
        first.mtime_ns + 1,
        "0" * 64,
    )
    snapshots = iter((first, second))
    monkeypatch.setattr(generator, "_read_once", lambda _path: next(snapshots))
    with pytest.raises(generator.EvidenceError, match="jsonl_not_stable_across_reads"):
        generator.stable_snapshot(jsonl, 0)
