# Tasks

## Specification

- [x] Add the circuit-open poison quarantine and eventless terminal settlement
      requirements to the `responses-api-compat` delta.
- [x] Record the cooldown/half-open, clean-close, full-resend, delta-only, and
      durable-anchor-clear scenarios.

## Implementation

- [x] Add the `retry_circuit_poisoned_anchor` quarantine reason and a TTL floor
      covering cooldown plus half-open lease.
- [x] Re-arm quarantine after a durable conflict merge opens the circuit.
- [x] Count eventless terminal frames through the attempt-scoped recorder and
      clear the poisoned durable anchor at the circuit threshold.
- [x] Do not charge a terminal frame when a verified safe full resend remains
      available; preserve the circuit generation until that replay dispatches.
- [x] Settle the circuit after confirmed abandonment only when all covered
      requests are stranded; retain it for an in-flight safe replay.

## Regression coverage

- [x] Cover two eventless failures opening/quarantining the key.
- [x] Cover clean-close non-quarantine and full-resend unanchored planning.
- [x] Cover quarantine lifetime, terminal strike accounting, and fenced anchor
      clearing.
- [x] Cover safe full-resend terminal recovery without an extra circuit strike.

## Verification

- [x] Run focused HTTP bridge unit/integration tests, Ruff, type checks, and
      OpenSpec validation.
- [ ] Record release image, Argo revision, health, and rollback evidence.
