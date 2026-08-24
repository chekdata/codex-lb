# Proposal: extend rowless semantic rebase to durable recovery markers

## Problem

When upstream rejects a completed saved response anchor, the bridge persists a
durable recovery marker. Exact owner-bound full-resend proof can recover some
requests automatically, but a request whose retained tool-call evidence does
not exactly settle the durable pending manifest must remain fail-closed. The
current service then repeats a stable no-progress error indefinitely because
the existing administrator-approved semantic-rebase flow only accepts a
missing durable checkpoint.

The marker proves the rejected anchor, owning account, stored checkpoint and
pending manifest. It does not prove whether an unmatched pending tool call had
an irreversible effect. Therefore this case needs an explicit operator
semantic rebase of the same durable generation, not weaker call-ID matching or
another automatic replay.

## Change

- Allow an active durable recovery marker to create one content-free rowless
  authority only after automatic exact proof has failed and only for an exact,
  self-contained, account-neutral root-task request with a settled retained
  request ledger.
- Bind the authority to the exact durable session row, API-key scope, account,
  rejected-anchor hash, task identity, request contract, projected payload and
  final serialized wire.
- Keep capture local: it performs no new account selection, WebSocket connect
  or upstream send and returns the existing administrator-authorization action.
- Accept the normal per-turn Codex turn-state affinity only after durable lookup
  resolves it to the exact hard session-header row and active marker; preserve
  the stricter identity gate for missing-row and child-thread recovery.
- Treat the official Codex 0.149 Responses-Lite namespace/tool-search envelope
  as account neutral only when its schema is closed and stateless. Accept the
  new session/thread/turn metadata keys only when session and thread values
  exactly match the already verified root-task identity.
- Make administrator and automatic recovery attempts compete for the same
  marker generation. The administrator claim uses a domain-separated digest
  over authority ID, generation and wire fingerprint so legacy equal-wire
  idempotency cannot admit both paths.
- Clear the marker attempt claim only after a physically proven unsent rollback
  or the atomic terminal checkpoint. Ambiguous delivery remains UNKNOWN and is
  never replayed.
- Expose marker-bound authority counts and the minimum rollback capability
  `rowless_marker_recovery_v2`; fail startup when the required schema column is
  absent and refuse schema downgrade while marker-bound authority exists.

## Non-goals

- Treating the marker as proof that an unmatched pending side effect did not occur.
- Relaxing pending call ID/type matching or PR #19's ambiguous-receive no-replay rule.
- Clearing an old anchor, marker or journal as an operational shortcut.
- Recovering multiple affected tasks concurrently.
- Treating arbitrary namespace tools, metadata drift, conversation/prompt
  references, or file/container/vector-store state as account neutral.
