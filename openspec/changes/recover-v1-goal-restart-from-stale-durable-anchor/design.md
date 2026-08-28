## Context

The production failure sequence used `/v1/responses` with an LB-generated
`http_turn_*` value and a durable thread session whose latest upstream anchor
was stale. The incoming automatic Goal request contained two or three
developer/user messages, no assistant output, no tool calls, and no file or
image state. It did not match the stored 13-item upstream prefix, so #1863
correctly rejected it as a durable full-history replay.

The Goal payload is nevertheless an explicit product-level restart. It carries
`<codex_internal_context source="goal">`, names the active objective, and says
that current worktree/external state is authoritative. The repository already
separates restart intent from replay safety:

- `responses_request_contains_goal_continuation_context` proves intent.
- `responses_payload_is_account_neutral_fresh_replay` proves that the canonical
  request carries no prior response, conversation, file/image, account-scoped
  input, unresolved tool dependency, or unknown wire field.

The remaining `/v1` problem is provenance. A marker alone is client-controlled,
and an arbitrary `x-codex-turn-state` must remain hard continuity. The route
therefore also requires the existing native Codex fingerprint, a logical
`thread-id`, and the exact LB-generated HTTP turn-state shape.

## Goals / Non-Goals

**Goals:**

- Let a verified native Codex Goal restart establish a new continuation
  without reading or modifying the stale durable anchor.
- Return a new turn state so later Desktop retries and turns naturally follow
  the replacement continuation.
- Preserve ordinary delta-only fail-closed behavior and exactly-once fences.
- Stop the deterministic same-anchor local rebind after an explicit
  `previous_response_not_found` rejection.
- Produce a structured startup error or a valid terminal SSE event instead of
  an abnormal 200 stream disconnect.

**Non-Goals:**

- Reconstructing missing conversation history for arbitrary delta-only
  requests.
- Deleting, clearing, rebinding, or migrating old durable sessions, aliases,
  anchors, operations, journals, or retry circuits.
- Allowing files, images, conversations, prior responses, or account-bound
  tool history to cross accounts.
- Changing WebSocket-native canonical error-code behavior or backporting the
  unrelated source-SSE normalization change.
- Adding a setting or globally changing sticky/continuity behavior.

## Decisions

### 1. Verify the restart at the `/v1` boundary

The route grants the escape only when all of the following are true:

1. `stream=true`.
2. The request has the existing native Codex user-agent/originator proof and
   no explicit OpenAI SDK fingerprint.
3. A nonblank logical `thread-id` is present.
4. The echoed turn state matches `http_turn_[0-9a-f]{32}`.
5. The canonical request contains the exact Goal marker.
6. The existing account-neutral fresh-replay classifier passes.

The final classifier already rejects `previous_response_id`, `conversation`,
file/image state, unknown item types, account-scoped metadata, and unresolved
tool history. The route does not duplicate those rules.

### 2. Rotate the turn state; do not mutate the old session

For the one verified request, copy the effective headers and replace only
`x-codex-turn-state` with a newly synthesized HTTP turn state. The ordinary
bridge path then creates a fresh hard turn key and performs a normal unanchored
first dispatch. The response already echoes the effective turn state, so no
new protocol field is needed.

This choice is smaller than adding a second recovery transaction and safer
than clearing the old anchor. The old durable row and aliases remain available
for audit or code rollback, while the new turn state is independently
addressable.

### 3. Deliver the terminal event instead of raising it into local rebind

The upstream-event path has already sanitized the stale response id, rewritten
the downstream response identity, settled the request, and persisted a terminal
`response.failed`. When the bridge consumer sees that specific rewritten event
with `propagate_http_errors=true`, it yields the event instead of raising
`bridge_previous_response_not_found`.

If it arrives during the route startup probe, the public route can return a
structured non-200 error before headers. If it arrives after the probe window,
the public stream normalizer produces a valid terminal SSE sequence. Because
no exception reaches `_stream_via_http_bridge`, the same-anchor
`previous_response_recover_local` branch is not entered.

Other first-event HTTP errors keep their existing startup behavior.

## Risks / Trade-offs

- **Risk:** A generic client forges the Goal XML. **Mitigation:** the marker is
  only one of six independent predicates; native fingerprint, logical thread
  identity, LB-generated turn-state shape, streaming mode, and the canonical
  account-neutral classifier are also required.
- **Risk:** A client-supplied turn state happens to match the synthesized
  shape. **Mitigation:** native Codex and `thread-id` proofs are additionally
  required; every foreign/non-synthesized value remains hard-bound.
- **Risk:** A valid stale delta now ends immediately instead of getting one
  local reconnect. **Mitigation:** an explicit `previous_response_not_found`
  proves the same anchor cannot succeed on that upstream path, while unsafe
  unanchored replay remains forbidden. Removing the identical redispatch
  reduces load without weakening continuity.
- **Trade-off:** A failure that arrives inside the startup probe may be JSON
  rather than SSE. Both forms are valid because headers have not been
  committed; after commitment the contract requires and emits a terminal SSE
  event.

## Rollback

Revert the code and deploy the prior immutable image. No data migration or
cleanup is required. Old durable state was never deleted or overwritten by the
escape path.
