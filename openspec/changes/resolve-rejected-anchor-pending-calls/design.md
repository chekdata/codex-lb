## Context

`http_bridge_sessions` already binds the API-key-scoped logical session,
account, latest completed response anchor, exact stored-input count and
fingerprint, pending-call manifest, and rejected-anchor marker.  Recovery uses
an owner-bound immutable proof, a marker wire compare-and-set, and the
`http_bridge_recovery_attempts` journal.

The upstream reader publishes `latest_response_id` only after
`response.completed`; an incomplete stream never becomes the durable anchor.
The observed rejection therefore concerns an earlier completed response.  In
the failing complete resend, the client physically retained the matching call
and output but also retained later bounded inputs.

## Decision

Keep the existing recovery state machine and extend only its exact settlement
predicate.

The predicate scans suffix prefixes and accepts the shortest self-contained
tool loop whose call and output maps both equal the durable pending manifest.
It compares the exact call-id-to-type bijection. The existing self-contained
request validator enforces each call/output ordering and rejects duplicates;
parallel call insertion order is not authority because the durable manifest is
canonically sorted. A later tool-like item, unrelated call, missing output,
unsupported status, or malformed caller cannot participate in the proof.

Once exact settlement is complete, the tail may be empty.  Otherwise it must
be either a bounded fresh user sequence, or one canonical retained
`agent_message` followed by that bounded sequence.  Existing developer
interleave compatibility remains restricted to its original exact three-item
call/developer/output window and cannot authorize a later follow-up.

All upstream admission still requires the same owner account, exact stored
prefix, exact full-input fingerprint, and durable marker.  The marker path
continues to claim one exact wire fingerprint and one journal attempt.  A new
terminal anchor stores the original complete-input checkpoint and clears the
marker through the existing transaction.  Failures and `UNKNOWN` attempts keep
the marker and remain non-replayable.

Rows with any rejected-anchor marker are conservatively excluded from ordinary
startup, closed-row, and abandoned-row deletion.  Terminal anchor publication
already clears the marker, after which normal retention resumes.

## Rejected alternatives

- **Operator abandonment receipt:** unnecessary for the observed shapes and
  introduces a new privilege and contract-fingerprint surface.  It remains a
  separate governed change if a future incident has no exact settlement proof.
- **Reconstruct deleted rows from logs:** logs do not contain durable ownership
  authority and cannot safely authorize replay.
- **Retry or capacity increase:** neither repairs the deterministic proof
  rejection and both risk repeated work.

## Rollout and rollback

This change adds no schema column or endpoint.  Rollback restores the narrower
predicate and prior cleanup behavior.  Existing marker/journal rows remain
compatible in either direction.

If an explicit stale anchor arrives after its durable row was already purged,
`durable_lookup` is absent and no sealed stored-prefix or pending-manifest proof
can be constructed. The gateway continues fail closed; it MUST NOT infer
authority from logs or from a merely full-resend-shaped input. A separate
change must give that condition a stable `recovery_authority_missing` outcome,
pause scheduled retries, and define a client-owned or separately privileged
same-logical-session checkpoint import before those historical sessions can be
recovered.
