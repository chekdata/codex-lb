# Recover verified `/v1` Goal restarts from a stale durable anchor

Source sidechat: `01a045d1-ecf5-7840-b404-4c9b6098216d`

Recovered from: `a347912b8334af52133bfc4436381fb78afb8888`

## Why

Codex Desktop custom providers use streaming `/v1/responses`. After the HTTP
bridge has returned an LB-generated `x-codex-turn-state`, Desktop echoes that
turn state on later requests. A synthetic persistent-Goal restart is a
self-contained request whose own instructions explicitly make the worktree and
external state authoritative, but `/v1` currently treats the echoed turn state
as hard continuity, resolves the old durable session, and injects its stale
`previous_response_id`.

The deployed stale-anchor recovery correctly refuses to remove that anchor
when the incoming request is only a delta relative to the stored upstream
history. It therefore reports `fresh_replay_available=false`. The residual bug
is that the verified Goal restart never reaches a fresh bridge even though the
existing Goal marker and account-neutral replay classifier independently prove
that this particular request may start a new continuation.

The same failure also exposes a terminal-delivery defect. An explicit upstream
`previous_response_not_found` is already rewritten and durably persisted as a
sanitized `response.failed`, but the public HTTP bridge raises that event as a
proxy exception, performs one local rebind with the same rejected anchor, and
can let the second exception escape after HTTP 200 SSE headers are committed.
Desktop then sees a disconnected stream instead of a deterministic terminal
failure.

## What Changes

- Recognize a streaming `/v1/responses` Goal restart only when the request is
  from a native Codex client, carries a logical thread identity, echoes an
  LB-synthesized `http_turn_<32 lowercase hex>` value, contains the exact Goal
  marker, and passes the existing account-neutral self-contained replay
  classifier.
- Replace the echoed stale turn state with a new LB-generated turn state for
  that one verified request. This creates a fresh unanchored bridge through the
  normal request path and returns the new turn state to the client without
  deleting or overwriting the old durable session, aliases, anchor, or
  operation ledger.
- Keep marker-only, generic `/v1`, non-streaming, foreign turn-state,
  previous-response, conversation, file/image, and account-scoped requests on
  the existing fail-closed path.
- When an explicit stale-anchor rejection has no verified replay, deliver the
  already-sanitized terminal failure instead of performing a same-anchor local
  rebind. Before headers this may become a structured startup error; after SSE
  starts it remains a valid terminal `response.failed` event.
- Keep the stored anchor and durable session unchanged on this failure path.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Adds a proof-gated fresh-continuation contract for
  native Codex Goal restarts on streaming `/v1/responses`, and requires an
  explicit stale-anchor rejection without a safe replay to terminate cleanly
  without a same-anchor redispatch.

## Impact

- Public `/v1/responses` route classification and effective turn-state headers.
- Responses HTTP bridge terminal delivery for explicit stale-anchor rejection.
- Product-path integration tests for fresh Goal restart and fail-closed
  terminal delivery.
- No schema, migration, setting, deployment prerequisite, dashboard, or
  destructive data operation.
