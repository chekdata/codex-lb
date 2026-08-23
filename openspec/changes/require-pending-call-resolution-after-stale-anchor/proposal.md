## Why

A long-lived scheduled Codex task can repeatedly submit delta-only follow-ups
after upstream has rejected the proxy-injected durable response anchor.  The
first rejection is fail-closed, but the rejection is currently remembered only
by the process-local quarantine.  After a bridge replacement, worker restart,
or quarantine expiry, the same durable anchor can be injected and rejected
again, so an automation can spend every wake-up rediscovering an already known
unrecoverable pending-call boundary.

## What Changes

- Persist a content-free, owner- and anchor-bound recovery-required marker on
  the durable HTTP bridge session when upstream first rejects a proxy-injected
  anchor and the request lacks a verified complete-context proof.
- Reject later unverified requests for that exact durable owner and anchor
  locally, before any upstream connection or dispatch, with a stable semantic
  error that requires pending-call resolution.
- Preserve the existing exact owner-bound full-resend and abandoned-pending
  proofs as the only paths allowed to recover the anchor on the same account.
- Clear the marker atomically when a successful replacement terminal response
  publishes its new durable anchor.
- Keep the rejected anchor, stored prefix fingerprint, and pending-call
  manifest intact; do not persist request text or introduce another proof
  verifier.

## Impact

- Scheduled delta wake-ups stop consuming upstream connections once the stale
  anchor is already known to require complete-context recovery.
- The recovery requirement survives process replacement and ordinary bridge
  lease expiry because it is durable session state.
- Existing PR #19 ambiguous-delivery exactly-once behavior is unchanged: no
  request is replayed from an ambiguous receive path.
