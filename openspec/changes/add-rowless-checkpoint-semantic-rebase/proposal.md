# Proposal: add rowless checkpoint semantic rebase

## Problem

An upstream can explicitly reject a client-supplied `previous_response_id`
before emitting any response event after the HTTP bridge session row that used
to hold the completed checkpoint has already been purged.  The service then
has no durable stored-prefix or pending-call manifest with which to prove an
automatic unanchored replay.  Repeating the request only repeats the rejected
anchor, while deleting the client thread loses task identity and context.

Logs are not continuity authority and the purged row cannot be reconstructed.
The safe recovery is therefore an explicit, dashboard-admin-authorized
**semantic rebase** of the same client thread, not an automatic replay and not
proof that an unknown historical side effect did or did not occur.

## Change

- Persist a content-free recovery authority outside the
  `http_bridge_sessions` foreign-key graph when an explicit client anchor is
  rejected with zero response events.  The authority is keyed by API-key
  scope, a strong session hash, and the rejected-anchor hash.
- Capture only canonical input count/fingerprint, non-input Responses contract
  fingerprint, ordered direct-call closure digest and unresolved count,
  projected-logical and exact post-transform wire digests, selected-account
  intent, generation/nonce, and eligibility flags.  Never persist request
  content or the plaintext anchor.  Requests with externally refetched image
  content are ineligible.
- Expose authenticated dashboard-admin list/challenge/approve endpoints.
  Approval binds one exact generation to a client-owned checkpoint receipt:
  authoritative session JSONL SHA-256/size/last offset, task/session identity,
  ordered tool ledger digest, and `unresolved_count=0`.
- Admit one same-thread retry only when its complete input and non-input
  contract exactly match the captured failed turn.  This recovery generation
  admits no suffix; ordinary follow-ups resume only after the retry publishes
  a new anchor.  Before account selection or WebSocket connect, atomically
  claim the approved non-cascading generation.  Bind the existing
  replacement-session UNKNOWN journal after durable replacement creation and
  before physical send.
- Recompute the exact serialized wire after the final account/image transform;
  a mismatch sends no bytes and can restore approval only through the
  physically-proven pre-send rollback.
- A proven pre-send failure restores APPROVED.  Any ambiguous post-send result
  remains UNKNOWN permanently.  `response.completed` publishes the ordinary
  durable session/aliases/new anchor/full checkpoint and consumes the semantic
  rebase generation.

## Safety classification

The operator acknowledgement states that the client-owned checkpoint is the
authority for continuing the task.  It does **not** assert that purged history
was recovered or that unknown old side effects were absent.  The one approved
dispatch is at-most-once within this generation; PR #19's ambiguous-receive
no-replay contract remains unchanged.

## Non-goals

- Reconstructing request content or authority from logs.
- Automatically authorizing a rowless recovery.
- Authorizing any account-scoped recovery; only account-neutral requests are eligible.
- Treating a new task/thread as recovery.
- Retrying an UNKNOWN dispatch.
