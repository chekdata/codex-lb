## Why

The durable stale-anchor marker correctly blocks repeated sends after an
upstream owner rejects a saved response anchor.  Its complete-resend verifier,
however, recognizes an exact pending call/output settlement only when that pair
is the entire suffix.  Real Codex transports retain canonical response-owned
reasoning and may append a bounded user or inter-agent follow-up after the exact
call/output pair.  Those requests contain physical settlement evidence but are
rejected and then remain locally blocked by the marker.

Ordinary retention cleanup can also delete a marker-bearing session row.  That
turns a recoverable, physically bound state into missing authority and defeats
the fail-closed recovery contract.

## What Changes

- Recognize the shortest suffix prefix that exactly and uniquely settles every
  durable pending call by call id and call type.
- After that exact settlement only, accept either no tail, a bounded user
  follow-up sequence, or one canonical retained `agent_message` followed by a
  bounded user sequence.
- Reject missing, duplicate, call/output-order-invalid, type-drifted,
  unrelated, or additional tool loops.  A generic developer/user boundary is
  not settlement proof. Parallel calls are compared as the durable exact
  ID-to-type bijection because the persisted manifest is canonically sorted.
- When a pending manifest is non-empty, remove the broader retained-output
  alternative: admission must use the exact manifest settlement path.
- Keep the existing owner/account/stored-prefix proof, marker wire CAS, and
  durable recovery-attempt journal unchanged.
- Exempt marker-bearing rows from ordinary startup, closed-row, and abandoned
  retention purges until a replacement terminal anchor clears the marker.

## Impact

- The three observed sanitized transport shapes become recoverable in place
  without a new operator API, credential class, client signature, or state
  machine.
- No unknown tool call is abandoned or replayed.  Recovery authority comes from
  the client's exact call/output settlement and the existing durable manifest.
- Historical rows already deleted by cleanup are not reconstructed from logs.
  An explicit stale-anchor request with no durable lookup has no stored-prefix
  or pending-manifest authority, so this change does not unanchor it. Those
  sessions require a separate governed checkpoint-rehydration contract.
