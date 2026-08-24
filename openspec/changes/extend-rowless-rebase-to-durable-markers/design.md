# Design: durable-marker administrator semantic rebase

## Authority and precedence

The existing automatic durable-marker proof remains the first recovery path.
Only when that proof fails may the service look up or capture an administrator
authority. Capture locks the origin `http_bridge_sessions` row and verifies its
exact API-key scope, account, recovery-required account, latest response hash,
marker anchor hash and empty marker-attempt claim. The new authority stores the
origin durable session ID without a cascading foreign key so its consumed
no-replay tombstone survives later bridge cleanup.

The authority is an explicit `operator_acknowledged_semantic_rebase`. It does
not assert that the pending call was never executed. The complete client
checkpoint and trusted operator acknowledgement select the semantic state from
which the task will continue.

## One marker generation, one winner

Automatic exact-proof recovery writes the raw request-text digest into
`recovery_required_attempt_fingerprint`. Administrator recovery writes:

```
SHA256(canonical_json({
  domain: "qk_http_bridge_rowless_marker_attempt_v1",
  authority_id,
  generation,
  wire_request_fingerprint
}))
```

Both paths lock and update the same durable marker row. The domains cannot be
mistaken for equal-wire idempotency. Administrator preflight commits the marker
claim and `APPROVED -> UNKNOWN` before account selection or WebSocket connect.
The later replacement-session journal remains UNKNOWN until terminal settlement.

## Failure behavior

A local setup failure before replacement binding restores APPROVED and clears
the administrator marker claim in one transaction. After journal creation, a
rollback requires an exact journal delete plus task-owner proof that the
initial send helper was never invoked; this also covers cancellation while the
send marker itself is committing. After the helper is invoked, the only
rollback authority is the typed transport result proving that every attempted
socket closed before its send primitive. Reconnect/setup failure before the
replacement send primitive uses that same physical proof. Cancellation is
deferred until the matching proven-unsent state has been durably restored.
The proof is a dedicated per-request state set only by the exact typed
closed-before-send result. A socket-only reconnect or a nonzero generic replay
counter is never physical non-delivery evidence.

Any zero-event disconnect, generic transport failure, timeout or exception
after a send primitive may have delivered the request. It leaves the authority,
marker claim and journal UNKNOWN permanently. No generic replay path may use
the administrator authority.

## Terminal and rollback floor

`response.completed` locks authority, origin marker and UNKNOWN journal, then
atomically publishes the new response anchor, full client checkpoint, response
alias, REPLAYED journal and CONSUMED authority while clearing all marker fields.
A persistence failure leaves the old anchor, marker claim, UNKNOWN journal and
authority intact.

Startup schema readiness requires
`http_bridge_rowless_recovery_authorities.origin_marker_session_id`, not merely
the table name. Migration downgrade refuses while any marker-bound authority
exists, including CAPTURED. The trusted status endpoint reports marker-bound
state counts and `rowless_marker_recovery_v2`; an older v1-only image is not a
valid rollback target after the first marker-bound capture.

Production recovery is serialized per affected task. The exact successor
source, immutable multi-platform digest, GitOps revision, workload image ID and
new rollback capability are read back before the first approval.
