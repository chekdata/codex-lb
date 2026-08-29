# Unify eventless anchor-poison settlement

Source sidechat: `01a04ac6-27a1-74b3-88e0-33b4022fc1ed`

Recovered from: `d1f4fa4ac3929e29bb531716a1964c39a5839793`

## Why

The HTTP bridge currently has separate reader-retirement and terminal-frame
paths for eventless failures. The retry circuit opens after two failures, but
the reader path still waits for the independent seven-failure anchor-poison
threshold. A reader failure followed by a terminal
`previous_response_not_found`, or the reverse order, can therefore open and
expire quarantine while the durable anchor remains available for reinjection.

## What changes

- Route every request-affecting, eventless, no-safe-replay failure through one
  attempt-scoped settlement decision, regardless of whether the reader or
  terminal path observes it first.
- Make the circuit-open transition atomically require durable anchor
  abandonment and quarantine. A failed or fenced anchor clear keeps the key
  fail-closed and prevents quarantine expiry from restoring the anchor.
- Remove the independent seven-strike reader-only poison decision; the circuit
  threshold is the sole threshold for a repeated eventless poisoned anchor.
- Fence stale session writers and make poison settlement idempotent so a
  concurrent reader/terminal callback cannot double-count or re-persist the
  cleared anchor.

## Impact

The behavior is default-on and scoped to hard-affinity HTTP bridge sessions.
Successful completed responses still reset the circuit and quarantine. Clean
close and midstream/eventful failures remain excluded from eventless anchor
poisoning. No operator threshold tuning or migration is required.
