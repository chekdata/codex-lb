## Context

The durable session already owns the minimum immutable authority needed for a
safe decision: API-key scope, account owner, latest response anchor, stored
input prefix fingerprint, and pending tool-call manifest.  It deliberately
does not retain conversation text.  The existing full-resend verifiers compare
an incoming complete request with that authority and are the only components
allowed to authorize a one-shot unanchored same-account recovery.

## Decision

Add one marker to the existing `http_bridge_sessions` owner rather than a new
state machine or receipt table.  The marker stores only the rejected anchor's
SHA-256 digest, the time it was observed, and at most one exact recovery wire
fingerprint.  Its owner is the session's existing `(api_key_scope, account_id)`
identity.  A fenced compare-and-set can bind `null -> wire fingerprint` only
while the durable row still owns the exact plaintext anchor that upstream
rejected.  The same wire is idempotent; a different otherwise-valid wire fails
closed for that marker generation.

Request admission reads the marker through the existing durable lookup.  When
the digest still matches `latest_response_id`, an incoming request must satisfy
one of the existing exact owner-bound full-resend proofs.  Otherwise admission
returns a stable non-retryable semantic error before session selection,
WebSocket connect, or send.  The marker is not time-expired independently of
the durable row: ordinary lease expiry must not make a known bad anchor eligible
for upstream redispatch.

When a verified recovery reaches `response.completed`, the existing atomic
anchor-registration transaction publishes the replacement response id and
clears the marker and its wire claim.  Failure before terminal completion
leaves the rejected anchor, marker, and wire claim intact.

## Constraints

- No request body, message content, tool arguments, credentials, or raw new
  identifiers are persisted by the marker.
- The marker does not authorize replay and cannot change accounts.
- It does not weaken stored-prefix, agent-message, pending-call, boundary, or
  ambiguous-delivery proof requirements.
- A different account, API-key scope, anchor, or owner epoch cannot set or
  clear another session's marker.

## Failure modes

- If the fenced marker write fails or ownership changes, the current request
  remains fail-closed through the existing persistence error path.
- If the marker columns are unavailable during a rolling upgrade, schema
  migration remains a deployment prerequisite; code does not emulate the
  marker in process memory.
- If replacement terminal persistence fails, the marker remains set and later
  delta requests remain locally blocked.

## Example

An automation submits a user delta while the durable row still references an
upstream anchor whose completed turn contains one undelivered tool call.
Upstream rejects that proxy-injected anchor.  The gateway records only the
anchor digest and time, then returns
`previous_response_pending_call_resolution_required`.  The next scheduled
delta receives the same local semantic error without opening a WebSocket.  A
later exact complete-context resend proves the pending call was never accepted,
recovers once on the owning account, and clears the marker only when the new
response anchor is committed.
