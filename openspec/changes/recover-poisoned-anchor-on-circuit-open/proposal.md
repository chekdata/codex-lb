# Recover poisoned HTTP bridge anchors when the retry circuit opens

Source sidechat: `01a04ac6-27a1-74b3-88d3-f1ede3901184`

Recovered from: `8c6ff6b6b4e76e453a2cfed29bd3b0ef0141a778`

## Why

The HTTP Responses bridge currently opens its hard-affinity retry circuit after
two eventless `stream_incomplete` or idle failures, while durable-anchor
poisoning waits for the independent default threshold of seven. Once cooldown
suppresses new submissions, the seventh failure is unreachable. The same dead
`previous_response_id` is therefore re-used by the next half-open probe and the
conversation remains wedged.

## What changes

- Add a circuit-open quarantine reason for eventless poison-class failures.
- Keep that quarantine active through the remaining cooldown and half-open
  probe lease, including when another replica's durable merge opens the circuit.
- When a full-conversation resend is planned under that quarantine, suppress
  every proxy-owned durable-anchor injection so the probe is sent unanchored;
  delta-only requests retain their anchor because it is their only context.
- Count eventless terminal error frames through the same attempt-scoped retry
  circuit path and clear the durable anchor once the circuit threshold is met,
  without charging requests that already emitted response events.

## Impact

The change is default-on and requires no setting or migration. It is scoped to
hard-affinity HTTP bridge keys and preserves account selection, reservation
settlement, and the existing 503 cooldown envelope. A successful unanchored
probe can complete normally and clear the temporary quarantine.
