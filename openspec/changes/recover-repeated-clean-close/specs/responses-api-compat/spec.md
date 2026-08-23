## MODIFIED Requirements

### Requirement: Clean upstream close before any response event fails fast

When the HTTP Responses bridge observes an upstream WebSocket close with `close_code = 1000` before any `response.*` event has been surfaced for the pending request, the proxy MUST preserve its existing pre-visible replay guards.
If the request has already used exactly one eligible pre-visible
replay and the replacement upstream WebSocket also closes cleanly before any
response event, the proxy MAY perform exactly one additional replay. The
additional replay MUST be hard-capped at one per request, and the configured
maximum MUST NOT raise that cap.

The proxy MUST NOT replay after downstream-visible output, after a terminal
response event, or when continuity-sensitive request state makes replay unsafe.
Before the additional replay, the proxy MAY sleep for bounded configured
jitter. The proxy MUST emit a dedicated low-cardinality diagnostic event for
the additional replay.

When a downstream HTTP stream task initiates pre-response recovery while the
upstream reader is blocked on the superseded socket, the proxy MUST cancel and
await that reader before locally closing the socket. It MUST then start exactly
one reader for the replacement socket. A close caused by replacing the socket
MUST NOT be recorded as an upstream clean-close failure, MUST NOT increment the
retry circuit, and MUST NOT retire pending work moved to the replacement. The
cancelled reader's socket-generation finalizer MUST NOT leave the shared session
marked closed while the replacement socket is being selected or opened, so idle
pruning MUST NOT evict the handoff in progress.

The default pre-response idle-recovery window MUST leave bounded headroom
before the downstream client's request timeout. With the default ten-second
keepalive interval, the proxy MUST initiate eligible recovery after no more
than six silent intervals so replacement connection and first output can occur
before a 120-second client deadline.

The stuck pre-response watchdog MUST judge staleness using elapsed time since
the last upstream activity and the absence of a response identifier or
`response.created` latency, not admission flags alone. A request with a prior
continuity anchor MUST receive at most two retire-thresholds of grace before
being considered stale. When the watchdog skips a candidate, it MUST emit a
low-cardinality diagnostic containing the session-closed state, candidate
count, and pending-state verdicts.

#### Scenario: clean close before response.created is not retried

- **GIVEN** an HTTP bridge request is not eligible under the pre-visible replay guards
- **WHEN** upstream closes the bridge with `close_code = 1000` before any
  `response.*` event for the pending request
- **THEN** the proxy returns HTTP 502 through the existing rejected-input path
- **AND** does not transparently replay the request

#### Scenario: clean close before response output receives one bounded additional replay

- **GIVEN** an HTTP bridge request has no surfaced `response.*` events
- **AND** its first pre-visible replay has already been used
- **WHEN** the replacement upstream WebSocket closes with code `1000`
- **THEN** the proxy performs one additional pre-visible replay
- **AND** the request replay count increases by one
- **AND** the proxy emits a `retry_precreated_clean_close` diagnostic event

#### Scenario: repeated clean closes do not create an unbounded replay loop

- **GIVEN** the additional clean-close replay has already been used
- **WHEN** another upstream WebSocket closes cleanly before response output
- **THEN** the proxy does not replay the request again
- **AND** the existing terminal or circuit handling is used

#### Scenario: visible output still prevents clean-close replay

- **GIVEN** the pending request has surfaced any response event downstream
- **WHEN** the upstream WebSocket closes with code `1000`
- **THEN** the proxy does not replay the request

#### Scenario: clean-close retry jitter is bounded

- **GIVEN** clean-close retry jitter is configured
- **WHEN** the additional clean-close replay is scheduled
- **THEN** the delay is no greater than the configured jitter maximum
- **AND** the hard replay cap remains one regardless of the configured value

#### Scenario: downstream idle recovery transfers reader ownership

- **GIVEN** the upstream reader is blocked on the current bridge socket
- **AND** the downstream HTTP stream task initiates eligible pre-response recovery
- **WHEN** the bridge replaces the upstream socket
- **THEN** the old reader is cancelled and awaited before its socket is closed
- **AND** the shared session remains live while the replacement socket opens
- **AND** idle pruning retains the registered session while the handoff is in progress
- **AND** exactly one reader owns the replacement socket
- **AND** the local close does not open or increment the retry circuit
- **AND** pending work remains attached to the replacement session

#### Scenario: silent pre-response recovery precedes the client timeout

- **GIVEN** the upstream has produced no response event
- **AND** the default ten-second keepalive interval is active
- **WHEN** six silent intervals elapse
- **THEN** the proxy initiates eligible pre-response recovery
- **AND** at least sixty seconds remain before a 120-second client request timeout

#### Scenario: anchored stuck-gate grace is bounded

- **GIVEN** a pending HTTP bridge request has a prior continuity anchor
- **AND** no response identifier or `response.created` latency has been recorded
- **WHEN** less than two retire thresholds have elapsed since the gate began waiting
- **THEN** the watchdog does not classify the request as stale
- **WHEN** two retire thresholds elapse without upstream activity
- **THEN** the watchdog may classify the request as stale

#### Scenario: upstream activity resolves admission-flag ambiguity

- **GIVEN** a pending request has not acquired the response-created gate
- **AND** upstream activity has not produced a response identifier or `response.created`
- **WHEN** the staleness threshold elapses
- **THEN** the watchdog classifies the request as stale
- **AND** emits pending-state verdict inputs when it skips a watchdog pass

### Requirement: Durable retry-circuit state protects repeated hard-affinity failures

For a hard-affinity bridge key, the proxy MUST scope retry-circuit state by
affinity kind, affinity key, and API-key scope (using a stable anonymous scope
when no API key is present). The proxy MUST record only the documented
pre-response failure classes (`stream_incomplete`, `clean_close`, and
`stream_idle_timeout`).

The default circuit MUST open after two consecutive recorded failures. Once
open, it MUST suppress pre-created replay until the persisted cooldown expires,
using exponential backoff from sixty seconds up to ten minutes. Clean-close
failures MUST cap their cooldown at thirty seconds. The proxy MUST persist
failure count, cooldown deadline, last failure detail, and update time in the
`http_bridge_retry_circuits` table and MUST merge conflict updates so concurrent
replicas cannot shorten an existing cooldown.

The clean-close retry jitter maximum MUST be read from the
`http_responses_session_bridge_clean_close_retry_jitter_max_seconds` runtime
setting and MUST be bounded to the inclusive range 0–30 seconds.

The proxy MUST evict process-local circuit entries and their loaded/persisted
markers after one hour without use, independently of durable-row cleanup, so
one-shot hard-affinity keys cannot grow the worker's memory without bound.

Before every hard-affinity retry decision, the proxy MUST refresh the durable
row so a cooldown opened by another replica is observed even when this process
has already loaded the key. A durable lookup or persistence failure MUST NOT
crash the request; the proxy MUST continue using available local state and
record the failure for observability. Rows older than one hour MUST be treated
as expired and removed. A successful terminal response MUST clear the local
and durable circuit state.

When a request is suppressed, the proxy MUST obtain the retry decision from one
atomic process-local snapshot after refreshing durable state. The decision MUST
include the normalized last failure class and remaining suppression interval.
State below the configured failure threshold MUST NOT suppress a request or
enter half-open mode, even if a stale durable read carries an older cooldown
timestamp. Once one half-open probe is admitted, its own streaming path MUST
NOT treat that probe lease as an active cooldown; the lease only fences later
submissions.
The HTTP response MUST expose the ceiling of that interval as `Retry-After`, and
the error message MUST name the recorded class (`stream_incomplete`,
`clean_close`, or `stream_idle_timeout`) accurately rather than calling every
failure a timeout. The same integer interval MUST be used in the message and
the response metadata. A client MAY retry after that interval; retrying earlier
MUST remain suppressed without dispatching another upstream request.

#### Scenario: the second hard-key failure opens a durable circuit

- **GIVEN** a hard-affinity key has one recorded pre-response failure
- **WHEN** a second eligible failure is recorded
- **THEN** the proxy opens the retry circuit
- **AND** persists at least two consecutive failures and a cooldown deadline
- **AND** subsequent pre-created replay is suppressed until that deadline

#### Scenario: retry decisions observe a cooldown opened by another replica

- **GIVEN** this replica previously looked up a hard-affinity key with no row
- **AND** another replica persists an open cooldown for that same key and API-key scope
- **WHEN** this replica evaluates the next pre-created retry
- **THEN** it refreshes durable state before deciding
- **AND** suppresses the retry for the persisted cooldown

#### Scenario: circuit state remains isolated by key and API-key scope

- **GIVEN** one hard-affinity key has an open circuit
- **WHEN** a different affinity key or API-key scope evaluates a retry
- **THEN** that request is not suppressed by the first key's circuit

#### Scenario: durable circuit lookup failure does not fail the request

- **GIVEN** durable retry-circuit lookup or persistence is unavailable
- **WHEN** the proxy evaluates or records a retry-circuit event
- **THEN** the request continues using any available local circuit state
- **AND** the failure is logged and exposed through retry-circuit observability

#### Scenario: incomplete streams produce an accurate cooldown response

- **GIVEN** a hard-affinity circuit is open with `last_detail = stream_incomplete`
- **WHEN** a new HTTP request is suppressed
- **THEN** the proxy returns HTTP 503 without dispatching upstream
- **AND** the message identifies repeated incomplete WebSocket streams
- **AND** `Retry-After` equals the ceiling of the same remaining interval named in the message

#### Scenario: terminal success clears the circuit

- **GIVEN** a hard-affinity circuit has recorded prior failures
- **WHEN** a request on that bridge reaches `response.completed`
- **THEN** local and durable circuit state are cleared
- **AND** the next request is not suppressed by the settled failures

#### Scenario: stale sub-threshold state is not an open circuit

- **GIVEN** a local or durable retry state has fewer failures than the open threshold
- **AND** it carries a stale cooldown timestamp
- **WHEN** the proxy evaluates admission and streaming startup
- **THEN** both paths admit the request without a cooldown response
- **AND** the state does not enter half-open mode

#### Scenario: admitted half-open probe does not suppress itself

- **GIVEN** an open circuit cooldown expires
- **WHEN** one half-open probe is admitted
- **THEN** later submissions remain fenced by the half-open lease
- **AND** the admitted probe's own stream startup proceeds without a synthetic 503

### Requirement: Proven pre-dispatch closes recover without duplicate turns

The upstream WebSocket adapter MUST distinguish a transport that is already in
a terminal closed state before the adapter invokes its underlying send
primitive. That condition MUST use a dedicated account-neutral error class and
MUST prove that the application send primitive was not called.

For an HTTP Responses request with no upstream response event, the bridge MAY
replace the closed socket once and dispatch the request on the replacement. A
continuity-bound request MUST still satisfy the existing proof-gated fresh-body
replay contract before its anchor can be removed or its account can change. The
replacement attempt MUST consume the request's single fresh-upstream retry
allowance.

A complete fresh-body replay proof and an account-neutral replay proof are
independent. If the retained fresh body still contains any account-scoped
identifier, the replacement MUST remain on the owning account even after the
continuity anchor is safely removed. Cross-account replacement is permitted
only when the retained body separately satisfies the account-neutral replay
contract and the replacement also uses a new account-neutral logical key with
all prior session and turn-state affinity headers removed. A hard-affinity
session that has not performed that explicit fork MUST remain on its owning
account even when its retained body is account neutral. A physical-socket-only
replacement that preserves the current logical key and reconnect handshake
MUST remain on the owning account for soft-affinity keys as well. It MUST NOT
treat body neutrality alone as proof that old turn state may cross accounts.

Any error raised after the underlying send primitive is invoked MUST remain an
ambiguous send failure. The proxy MUST NOT reconnect and resend from that path,
even when the exception reports a clean WebSocket close, because the complete
frame may already have crossed the kernel boundary.

For an exact stored-prefix full resend owned by the same durable or live
session, a completed Codex inter-agent delivery MAY serve as the retained
prior-output boundary when it has the exact response-owned `agent_message`
shape: an `amsg_` UUID identity, distinct canonical absolute agent paths for
author and recipient, exact `turn_id` plus finite `create_time` metadata, and
one self-contained `input_text` content part. The proof MUST reject missing,
extra, malformed, reordered, or client-shaped fields. This shape MUST remain
owner-bound and MUST NOT by itself make a request eligible for account-neutral
reallocation. The proof MUST additionally bind a persisted tool-call manifest
that is present and exactly empty; a missing manifest or any unsettled call
MUST reject the inter-agent boundary. A matching request MAY remove only the
proxy-injected stale `previous_response_id`, replay the complete context once
on the same account, and publish a replacement anchor only after
`response.completed`.

For the abandoned-pending-call exception, the new suffix MAY contain zero or
more exact response-owned reasoning items immediately before the canonical
`agent_message`. Each such item MUST carry an `rs_` identity, encrypted
content, a structured summary, and exact internal turn provenance; malformed
reasoning or reasoning after the agent boundary MUST fail closed. The proof
projection MUST omit those reasoning items and any exact historical
response-owned agent deliveries already covered by the immutable stored-prefix
fingerprint. The actual one-shot unanchored retry MUST use that same projected
input. It MUST NOT resend omitted response bookkeeping or any pending call id.
User follow-ups in this proved suffix MAY carry Codex's exact persisted message
bookkeeping: a `msg_` UUID, `user` role, one self-contained `input_text`, and
exact `turn_id` plus finite nonnegative `create_time` metadata. The proof MUST
validate that complete raw shape before projection. The projected replay MUST
remove the response-owned message id and `create_time` while preserving the
validated `turn_id`; missing, extra, malformed, or non-finite fields MUST fail
closed.

Rejected-proof observability MUST classify only known string-valued `type` and
`role` fields. Non-string or otherwise malformed values MUST be labeled as an
opaque `other` shape without logging their content and MUST NOT turn the
fail-closed bridge response into an internal server error. Pre-bridge request
inspection, including file-reference extraction, MUST apply the same typed
classification and MUST NOT fail on non-string item types.

#### Scenario: socket already closed before send recovers once

- **GIVEN** an HTTP bridge socket is already closed before `response.create` dispatch
- **AND** the request is eligible for fresh-upstream recovery
- **WHEN** the adapter is asked to send the request
- **THEN** the adapter does not invoke the closed socket's send primitive
- **AND** the bridge opens one replacement socket
- **AND** the request is dispatched exactly once on that replacement

#### Scenario: a second pre-dispatch close does not loop

- **GIVEN** the request consumed its one fresh-upstream recovery allowance
- **WHEN** the replacement socket is also already closed before send
- **THEN** the proxy does not open a third socket
- **AND** the request fails through the existing terminal transport path

#### Scenario: completed inter-agent delivery recovers an exact long-session resend

- **GIVEN** a live or durable session stores an exact completed input prefix
- **AND** no tool-call manifest remains pending
- **AND** the full resend suffix contains response-owned reasoning, one
  canonical completed `agent_message`, and one or more later user messages
- **WHEN** upstream rejects the proxy-injected stale `previous_response_id`
  before any response event
- **THEN** the proxy sends the original complete request exactly once without
  `previous_response_id` on the same owning account
- **AND** preserves the canonical inter-agent message in that request
- **AND** publishes a new anchor only after successful completion

#### Scenario: malformed inter-agent lookalikes remain anchored

- **GIVEN** a full-resend-shaped suffix contains an `agent_message` with a
  missing or non-`amsg_` identity, invalid agent path, non-finite timestamp,
  extra metadata, hosted/account-scoped content, or a user item before it
- **WHEN** stale-anchor recovery is evaluated
- **THEN** the inter-agent item does not satisfy retained-output proof
- **AND** the proxy keeps the continuity request owner-bound and fail-closed
- **AND** it does not dispatch an unanchored replay

#### Scenario: live, represented, or unknown tool state rejects an inter-agent boundary

- **GIVEN** a full resend suffix contains a canonical completed `agent_message`
- **AND** the durable or live owner has an unavailable persisted tool-call
  manifest, the anchor has not been explicitly rejected, or the exact client
  resend contains any pending call id
- **WHEN** stale-anchor recovery is evaluated
- **THEN** the inter-agent item does not satisfy retained-output proof
- **AND** the existing continuity anchor remains attached

#### Scenario: rejected orphan pending call yields to a later inter-agent boundary

- **GIVEN** the durable owner has a nonempty pending client-side tool-call manifest
- **AND** an exact-prefix full resend contains none of those pending call ids
- **AND** its fresh suffix begins with one canonical response-owned
  `agent_message` (optionally preceded only by exact response-owned reasoning)
  followed only by one or more new user inputs
- **WHEN** upstream rejects the exact durable `previous_response_id` before
  emitting any response event
- **THEN** the bridge treats the pending call as never accepted or executed by
  the client
- **AND** retries the complete request exactly once without the stale anchor
  on the same owning account
- **AND** does not synthesize the orphan call or output into the unanchored replay
- **AND** omits the response-owned reasoning and sealed historical agent
  deliveries from the one-shot recovery payload
- **AND** publishes a replacement anchor only after successful completion

#### Scenario: persisted user bookkeeping is validated then normalized

- **GIVEN** the proved abandoned-pending suffix contains one or more persisted
  user messages with canonical `msg_` UUIDs and exact turn/timestamp metadata
- **WHEN** the bridge builds the one-shot same-owner unanchored projection
- **THEN** it first validates every raw user-message field and content part
- **AND** removes each response-owned message id and `create_time`
- **AND** preserves only the validated `turn_id` metadata in the replay
- **AND** any malformed id, timestamp, metadata, content, or extra field rejects
  the proof before dispatch

#### Scenario: malformed diagnostic fields remain fail-closed

- **GIVEN** a rejected full-resend suffix contains a non-string `type` or
  `role` value
- **WHEN** the bridge emits its bounded suffix-shape diagnostic
- **THEN** it records only the `other` classification
- **AND** does not expose the malformed value
- **AND** does not return HTTP 500 from the diagnostic path

#### Scenario: complete but account-scoped fresh body stays on its owner

- **GIVEN** a continuity-bound request has a complete fresh-body replay proof
- **AND** that fresh body still contains an account-scoped conversation,
  prompt, hosted input item, or file identifier
- **WHEN** the original socket is proven closed before send
- **THEN** the bridge may remove the continuity anchor for the one-shot retry
- **AND** the replacement remains bound to the original owning account

#### Scenario: only an account-neutral fresh body may change accounts

- **GIVEN** a continuity-bound request has both a complete fresh-body replay
  proof and a separate account-neutral replay proof
- **WHEN** the original socket is proven closed before send
- **AND** the bridge establishes a new account-neutral logical key and strips
  all prior session and turn-state affinity headers
- **THEN** the one-shot replacement may select another eligible account

#### Scenario: hard affinity stays on its owner without an explicit fork

- **GIVEN** a continuity-bound request has an account-neutral fresh-body proof
- **AND** its current bridge still has a hard session or turn-state affinity key
- **WHEN** the original socket is proven closed before send
- **THEN** the replacement remains on the original owning account
- **AND** no old session or turn-state identifier is sent to another account

#### Scenario: soft affinity stays on its owner without an explicit fork

- **GIVEN** a continuity-bound request has an account-neutral fresh-body proof
- **AND** its current bridge has a soft prompt-cache or sticky affinity key
- **WHEN** the original socket is proven closed before send
- **AND** recovery replaces only the physical socket while retaining the
  current logical key and reconnect handshake
- **THEN** the replacement remains on the original owning account
- **AND** no old session or turn-state identifier is sent to another account

#### Scenario: post-dispatch close remains non-replayable

- **GIVEN** the adapter invoked the underlying send primitive
- **WHEN** that primitive raises a clean-close or transport exception
- **THEN** delivery is treated as ambiguous
- **AND** the proxy does not dispatch the request on another socket

#### Scenario: unclassified receive error after dispatch remains non-replayable

- **GIVEN** the adapter has successfully invoked the underlying send primitive
- **AND** the bridge has not observed `response.created` or another response
  event
- **WHEN** the WebSocket reader reports a generic transport error without a
  classified protocol error code
- **THEN** upstream acceptance is treated as ambiguous
- **AND** the proxy does not reconnect or resend the request
- **AND** it terminally settles the affected request and retires the bridge so
  a later client request can create a fresh session

#### Scenario: explicit clean close keeps its bounded recovery owner

- **GIVEN** a pre-visible request satisfies the existing clean-close replay
  guards
- **WHEN** the reader reports an explicitly classified clean close
- **THEN** the clean-close owner may perform its single bounded recovery
- **AND** the generic receive-error fail-closed rule does not consume or widen
  that recovery allowance

#### Scenario: idle generic receive failure is not an active stream failure

- **GIVEN** a bridge has no pending request and no create-admission waiter
- **WHEN** its reader reports a generic transport error
- **THEN** the bridge retires its stale aliases without reconnecting or opening
  a retry circuit
- **AND** it emits a content-free informational retirement diagnostic rather
  than an active `stream_incomplete` warning

### Requirement: Rejected proxy continuity anchors recover without context loss

When the upstream explicitly rejects a proxy-injected `previous_response_id` before any response event is observed, the bridge MUST NOT inject the
same rejected identifier into another physical WebSocket. A request that has a
request-bound immutable durable proof covering its complete unanchored input
MAY bypass the rejected anchor and replay exactly once, without that anchor, on
the same account. The bridge MUST retain the previous durable anchor until the
replacement reaches terminal completion and atomically publishes its new
checkpoint.

When that proof is absent, the bridge MUST quarantine the logical session key,
retire the rejected physical bridge while preserving the durable lease, and
return a stable non-retryable-same-contract error. A later full-resend-shaped
client request MUST take a fresh unanchored path only when its request-bound
durable proof exactly matches the current durable owner and proves complete
conversation context. A merely full-resend-shaped request and a delta-only
request MUST keep the durable anchor and fail closed; the bridge MUST NOT
silently discard prior conversation context. A completed response MUST clear
the bounded quarantine.

Client-supplied `previous_response_id` values MUST NOT be cleared or replayed
unanchored by this recovery path unless the same request carries a
request-bound immutable durable proof that matches the current durable owner,
the exact stored input prefix, pending-tool manifest, and complete fresh
suffix. When that proof exists and upstream rejects the explicit anchor before
any response event, the bridge MAY quarantine the rejected physical session
and replay the proved complete request exactly once without the anchor on the
same owning account. An incomplete, delta-only, owner-conflicting, or
unproved explicit-anchor request remains fail-closed.

A nonempty durable pending-tool manifest MAY satisfy this stale-anchor recovery
only when the exact client resend contains none of its call ids, its fresh
suffix begins with one canonical response-owned `agent_message` and then only
new user input, and upstream first rejects the exact anchor before emitting any
response event. This narrow condition proves that the client advanced without
accepting or executing the orphan call. It MUST NOT make the request eligible
for proactive, owner-unavailable, cross-account, or pre-rejection fresh replay.

#### Scenario: proved complete request recovers in the same turn

- **GIVEN** the upstream rejects a proxy-injected durable response anchor before any response event
- **AND** immutable durable evidence proves the untrimmed request contains the complete conversation context
- **WHEN** the bridge performs local recovery
- **THEN** it retains the previous durable anchor until replacement completion
- **AND** replays the complete request exactly once without an anchor on the same account
- **AND** terminal completion atomically replaces the durable anchor
- **AND** a failed replacement leaves the previous durable anchor available for a later verified retry

#### Scenario: unproved request quarantines and only a durably proved full resend recovers

- **GIVEN** the upstream rejects a proxy-injected durable response anchor before any response event
- **AND** the current request lacks complete-context proof
- **WHEN** the bridge handles the rejection
- **THEN** it returns `previous_response_anchor_unrecoverable` without another upstream dispatch
- **AND** quarantines the logical key without clearing its durable anchor
- **WHEN** the client subsequently supplies a full-resend-shaped request whose immutable proof exactly matches the durable owner and complete context
- **THEN** the fresh bridge sends that request without the rejected anchor
- **AND** a terminal completion clears quarantine

#### Scenario: delta-only and client-owned anchors remain fail-closed

- **GIVEN** a client explicitly supplies `previous_response_id`
- **AND** its request omits the prior completed output or otherwise lacks the
  immutable owner-bound complete-context proof
- **WHEN** upstream rejects that anchor before execution
- **THEN** the bridge does not clear the anchor or dispatch an unanchored copy
- **GIVEN** a quarantined key receives a delta payload, a full-resend-shaped
  payload without exact durable completeness proof, or an unproved
  client-supplied anchor
- **WHEN** recovery is evaluated
- **THEN** the proxy does not remove the anchor
- **AND** it does not issue an unanchored replay that could omit prior context

#### Scenario: proved explicit stale anchor recovers once

- **GIVEN** a client explicitly supplies `previous_response_id`
- **AND** the same request's immutable durable proof binds the current owner,
  exact stored prefix, pending-tool manifest, and complete fresh suffix
- **WHEN** upstream rejects that anchor before any response event
- **THEN** the bridge retires the rejected physical session
- **AND** sends the proved complete request once without
  `previous_response_id` on the same owning account
- **AND** a failed replacement cannot authorize a second duplicate replay

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a
transient upstream error for the account before signaling failure for those
pending requests, except when the close carries a classified process-wide
network failure, is a clean close (`close_code = 1000`) before any
`response.*` event, or carries the classified per-socket
`upstream_keepalive_timeout` transport error. Clean pre-response closes and
keepalive timeouts MUST remain account-neutral while using the bounded retry
and retry-circuit handling above. A classified process-wide network failure
MUST remain account neutral and use its network error code. For other closes,
the proxy MUST surface
`stream_incomplete` to affected pending requests.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence,
  or the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close is not an account-neutral clean pre-response close,
  process-wide network failure, or upstream WebSocket liveness timeout
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: sequenced direct websocket closes before completion

- **GIVEN** a direct Responses WebSocket request has successfully emitted a
  finite integer `sequence_number`
- **WHEN** the upstream websocket closes before a terminal response event is observed
- **AND** the close is not an account-neutral clean pre-response close,
  process-wide network failure, or upstream WebSocket liveness timeout
- **THEN** the request is recorded as failed with `stream_incomplete`
- **AND** no synthetic terminal frame is emitted under the active response id
- **AND** the downstream WebSocket closes with code 1011
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: websocket liveness timeout remains account neutral

- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** its transport reports `upstream_websocket_liveness_timeout`
- **THEN** the pending request fails with that classified error code
- **AND** the account receives no failure-health signal
- **AND** the request is not transparently replayed

#### Scenario: clean pre-response close does not penalize the account

- **GIVEN** a hard-affinity HTTP bridge request is pending with no surfaced response event
- **WHEN** the upstream websocket closes cleanly before response output
- **THEN** the proxy records the clean-close retry-circuit outcome
- **AND** the selected account is not penalized
