# Design

The retry circuit owns the repeated eventless failure transition. When a hard
affinity key reaches the circuit threshold for a poison-class failure, it must
record one durable `OPEN` generation and obtain a fenced anchor-clear result
before allowing a recovery probe. Reader and terminal paths call the same
attempt-scoped recorder, so their ordering cannot change the outcome.

If the clear is fenced out or unavailable, the key remains quarantined and the
durable circuit generation remains open. Anchor injection is denied while that
generation is unresolved; the next recovery attempt retries the fenced clear or
uses an explicitly unanchored full replay. Quarantine may be cleared only by a
completed response after the durable anchor has been confirmed absent.

The implementation must preserve single-settlement ownership: one physical
response-create attempt contributes at most one strike, and a successful
`response.completed` resets the local and durable circuit state.
