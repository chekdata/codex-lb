# Recognize canonical invalid previous-response anchors

## Why

Production upstream now emits a stale Responses anchor as `code=invalid_request_error` with the exact message `Invalid previous_response_id.` and no `param`. The existing classifier only recognizes `previous_response_not_found` or the older `param=previous_response_id` plus “not found” shape. As a result, the HTTP bridge forwards a raw 400, then treats the socket close as `stream_incomplete` and opens the per-session retry circuit instead of entering the existing proof-gated stale-anchor recovery path.

## What Changes

- Recognize only the exact canonical invalid-`previous_response_id` message when `param` is absent or matches `previous_response_id`.
- Route that shape through the existing proof-gated recovery, quarantine, masking, and settlement behavior.
- Keep unrelated `invalid_request_error` shapes request-owned and non-replayable.
- Add unit and public HTTP bridge regressions for the exact production event.

## Non-Goals

- Do not broaden recovery to arbitrary invalid requests.
- Do not drop client-supplied anchors without the existing complete-context proof.
- Do not weaken duplicate-replay, account-ownership, or durable-settlement fences.
