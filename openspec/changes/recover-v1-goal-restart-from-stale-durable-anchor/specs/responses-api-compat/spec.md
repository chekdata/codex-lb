## ADDED Requirements

### Requirement: Verified native `/v1` Goal restarts establish a fresh continuation

A streaming `/v1/responses` request MAY abandon its echoed HTTP turn-state
continuation only when the request is proven to be a native Codex Goal restart.
The proof MUST require the native Codex client fingerprint, a logical thread
identity, an echoed LB-synthesized `http_turn_<32 lowercase hex>` value, the
exact Goal-continuation marker, absence of an explicit OpenAI SDK fingerprint,
and successful classification by the existing account-neutral self-contained
fresh-replay predicate.

For a proved request, the proxy MUST assign a new LB-synthesized HTTP turn
state and dispatch the request once without the old durable
`previous_response_id`. The response MUST return the new turn state, and a
follow-up that echoes it MUST continue on the new bridge. The proxy MUST NOT
delete, clear, overwrite, or rebind the old durable session, aliases, anchor,
operation ledger, recovery journal, or retry circuit in order to authorize the
restart.

A marker without every other proof, a generic `/v1` client, a non-streaming
request, a missing logical thread identity, a foreign turn state, or a request
with previous-response, conversation, file/image, account-scoped, unknown, or
unresolved tool state MUST retain the ordinary continuity and fail-closed
behavior.

#### Scenario: Verified Desktop Goal restart rotates away from a stale durable turn

- **GIVEN** a native Codex Desktop `/v1/responses` stream echoes an
  LB-synthesized HTTP turn state whose durable session contains a stale anchor
- **AND** the request carries a logical thread identity and the exact Goal
  marker
- **AND** the canonical request is an account-neutral self-contained fresh
  replay
- **WHEN** the request is admitted
- **THEN** the proxy assigns and returns a different LB-synthesized HTTP turn
  state
- **AND** it dispatches the Goal request once without the old anchor
- **AND** the old durable session, aliases, anchor, and operations remain
  unchanged
- **AND** a follow-up using the returned turn state continues on the new bridge

#### Scenario: Goal marker with account-scoped state cannot rotate continuity

- **GIVEN** a `/v1/responses` request contains the exact Goal marker
- **AND** it also contains a previous response, conversation, file/image,
  account-scoped field, unknown input shape, or unresolved tool dependency
- **WHEN** the request is classified
- **THEN** the request MUST NOT receive a fresh turn state by this contract
- **AND** existing owner and continuity rules remain authoritative

#### Scenario: Foreign or generic `/v1` clients cannot claim Goal restart authority

- **GIVEN** a request lacks the native Codex fingerprint or logical thread
  identity, or carries a turn state outside the LB-synthesized HTTP shape
- **WHEN** it includes a Goal-like marker
- **THEN** the marker MUST NOT authorize anchor bypass or fresh continuation
- **AND** the supplied turn state remains ordinary client continuity input

### Requirement: Unreplayable stale HTTP continuations terminate without identical redispatch

When the Responses HTTP bridge receives an explicit
`previous_response_not_found` rejection for a request that has no verified safe
fresh replay, the proxy MUST retain the durable anchor and MUST NOT dispatch a
second request carrying that same rejected anchor. The already-sanitized
failure MUST be delivered as a structured startup error before HTTP headers are
committed or as a valid terminal `response.failed` event after SSE streaming
has begun.

The failure MUST use the current downstream response identity, MUST NOT expose
the raw upstream error envelope or stale response id, and MUST leave the old
durable session, aliases, operation ledger, recovery journal, and retry circuit
intact. Verified full-history recovery defined by the existing stale-anchor
contract remains unchanged.

#### Scenario: Delta-only stale rejection ends once and preserves the anchor

- **GIVEN** the bridge injects a durable previous-response anchor into a
  delta-only request
- **AND** upstream explicitly rejects that anchor as not found before emitting
  visible output
- **AND** no verified fresh replay is available
- **WHEN** the terminal failure is processed
- **THEN** exactly one anchored upstream dispatch occurs
- **AND** no `previous_response_recover_local` same-anchor redispatch occurs
- **AND** the client receives a structured startup error or valid terminal SSE
  failure instead of an abnormal stream disconnect
- **AND** the old durable anchor and operation settlement remain intact

#### Scenario: Verified full resend still uses the existing fenced recovery

- **GIVEN** an explicit stale-anchor rejection has a verified durable full
  resend and all required operation-fence and spool-reset proofs
- **WHEN** the existing stale-anchor recovery runs
- **THEN** this terminal-delivery requirement MUST NOT suppress its one bounded
  unanchored replay
- **AND** all existing account-neutral, same-owner, file-pin, tool-settlement,
  circuit-generation, and exactly-once constraints remain authoritative
