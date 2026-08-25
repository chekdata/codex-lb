# Proposal: auto-recover proofed live Codex turns

## Problem

The rowless semantic-rebase fence currently returns an administrator-approval
error after an eventless stale-anchor rejection. Official Codex Desktop then
finishes the local turn and may rebuild a different request on retry. The
original exact request is lost even when the gateway already proved that the
live request is a root task, self-contained, account-neutral, and has a fully
settled direct-call ledger. Requiring a dashboard click does not repair that
request loss and exposes a deterministic HTTP 400 as a misleading reconnect.

An older APPROVED authority can have the same problem: if it has never reached
dispatch, a later official live request may contain a newer complete checkpoint
that cannot match the old whole-input fingerprint. Keeping the old generation
forever makes the original task unrecoverable even though no upstream semantic
rebase was attempted.

## Change

- Treat a validated official root-turn metadata carrier as eligibility for an
  automatic live semantic rebase. The request must still pass every existing
  self-contained, account-neutral, exact-identity, zero-unresolved-call, and
  eventless stale-anchor proof. Its complete resend must also retain prior
  assistant or agent output before a fresh user follow-up; an incremental-only
  input remains on the operator path.
- Keep the exact anchor-free wire in memory and use the existing durable
  authority, preflight CAS, replacement journal, send marker, and terminal
  settlement before performing one in-place replay on the same downstream
  request.
- Allow a CAPTURED or APPROVED generation to be replaced by a newer live
  capture only when the old generation is physically proven never dispatched:
  no dispatch request, no replacement binding, no send marker, no recovery
  journal, and no durable-marker attempt claim. Increment the generation and
  write a content-free audit record before claiming the new wire.
- Record whether dispatch authorization came from an operator checkpoint or
  an automatic live-request proof. Preserve the existing operator APIs for
  legacy and forensic use.
- Keep UNKNOWN and CONSUMED generations monotonic. An ambiguous send, identity
  conflict, child lineage, incomplete input, unresolved tool call, changed
  account, or changed wire remains fail closed and is never auto-replayed.

## Non-goals

- Disabling the exactly-once fence.
- Trusting logs, UI retry state, or unvalidated client metadata as authority.
- Automatically retrying an UNKNOWN or post-send request.
- Importing a different task or creating a replacement Codex thread.
- Treating a new user message without retained completed output as a complete
  replayable history.
