## Why

The durable HTTP Responses checkpoint records the completed input fingerprint
and pending tool-call map, but not the ordered response output that produced
that pending-call state. After an idle reconnect, official Codex can resend the
exact stored prefix followed by response-owned reasoning, assistant commentary,
the pending call and its output, and one or more later retry turns. The current
verifier cannot bind the reasoning/commentary items to the completed response,
so it falls back to rowless semantic inference.

Rowless automatic recovery has consequently accumulated separate allowlists for
root retries, settled-tool tails, staged partial responses, Lite developer
ordering, and other incident-specific layouts. Each new official client layout
requires another grammar branch even though codex-lb observed the original
`response.completed` output and could have retained a content-free proof of it.

## What Changes

- Build a versioned, content-free response transition manifest from every
  eligible `response.completed` output and bind it to the durable checkpoint.
- Persist only ordered canonical item fingerprints and bounded structural
  metadata; never persist response text, reasoning, tool arguments, or tool
  output.
- Verify a full resend by matching its exact stored prefix and completed-output
  manifest, then proving the durable pending calls are settled and later turns
  are canonical, task-bound, self-contained, and ledger-clean.
- Permit one same-request, same-account replay without the rejected proxy anchor
  when that manifest proof remains exact.
- Keep checkpoints without a valid manifest on the existing fail-closed operator
  recovery path. Do not add another rowless sequence allowlist for this change.
- Cover official Codex retry layouts with table-driven and generated transition
  tests so ordering variations are validated by identities and state, not by a
  growing list of positional patterns.

## Impact

- A database migration adds nullable manifest fields to durable HTTP bridge
  sessions and recovery markers. Existing rows remain valid legacy checkpoints.
- The response completion and stale-anchor recovery paths gain manifest build,
  persistence, matching, and observability.
- Automatic recovery becomes more general for newly completed responses while
  remaining exactly-once, account-bound, and fail-closed for missing or
  ambiguous evidence.
