# Design: proof-gated live semantic rebase

## Eligibility

Automatic recovery is narrower than ordinary rowless capture. It requires all
existing rowless capture and root-identity checks plus a valid official
`x-codex-turn-metadata` carrier whose request kind is `turn` and whose
`workspace_kind` is a nonblank bounded string. Body and direct carriers must
agree. Parent, fork, and subagent lineage remains ineligible.

The complete request must have a canonical account-neutral projection, a
self-contained history, and a direct-call ledger with zero unresolved,
duplicate, orphaned, or type-mismatched calls. External refetchable images and
account-scoped resources remain ineligible. It must retain at least one prior
assistant message or agent message followed by a fresh user turn. A request
that contains only the incremental user input is insufficient proof that
completed output and settled call results were retained, even if it is
otherwise self-contained.

Official Codex may persist several failed root-turn inputs after a completed
assistant final answer before another live recovery request reaches the
gateway. Such a tail remains eligible only when every item is a canonical
response-owned user or developer message with a unique valid message ID, the
tail contains at least two user messages, starts and ends with user input, and
never contains consecutive developer messages. Assistant output, tool
activity, unknown items, duplicate IDs, and malformed ordering remain
ineligible. All other automatic recovery gates still apply independently.

## Live first-attempt recovery

When upstream rejects the explicit anchor before `response.created` and before
any response event, the reader still owns the original downstream request and
its exact serialized body. It atomically creates and preflight-claims an
automatic authority, retaining only content-free hashes in the database. The
anchor-free projected body remains in memory.

The current bridge reconnects on the same selected account. Before the second
physical send, it binds the durable replacement-session UNKNOWN journal and
persists the send marker. A wire mismatch, setup failure, or cancellation
before the send primitive uses the existing physically-unsent rollback. Once
the send primitive may have been reached, the authority remains UNKNOWN unless
terminal settlement proves completion.

The stale upstream failure is never delivered when the in-place replay was
successfully submitted. The original HTTP/SSE request receives the recovered
events and `response.completed`, so Codex Desktop records the new anchor on the
same local turn instead of entering its generic reconnect path.

## Unsent generation replacement

An existing CAPTURED or APPROVED authority may be replaced only inside one
transaction that locks both the authority and any bound durable marker. The
transition requires:

- the same API-key scope, stable task authority, task/session identity,
  selected account, rejected anchor, and marker origin;
- no replacement session, dispatch request, wire claim, send timestamp, or
  consumed timestamp;
- no marker attempt fingerprint; and
- a new request that independently satisfies automatic eligibility.

If the request contract changed, the transaction writes a content-free audit
record for the old generation, increments the generation, installs the new
capture hashes, and clears the old operator challenge/receipt fields. It then
records an automatic authorization proof and transitions directly to UNKNOWN
with the new request/wire preflight claim. Concurrent requests lose the CAS.

UNKNOWN and CONSUMED authorities are never replaceable. An operator-approved
generation that already started any dispatch is also never replaceable.

Official Codex can omit `previous_response_id` while a hard-session durable
marker still requires reattachment. After owner and marker validation inject
the stale anchor, automatic preflight may use that verified anchor while
retaining the original anchorless payload as the request-contract evidence.
Once the semantic rebase claims its anchor-free wire, the temporary injection
state is cleared so store-context trimming cannot rewrite the claimed wire or
discard its authority settlement identity.

## Authorization provenance

Two nullable columns distinguish `operator_checkpoint` from
`automatic_live_request` authorization and bind a SHA-256 proof. Existing
approved rows are backfilled as operator checkpoint authorization. Dispatch
accepts either mode only when its corresponding proof is present.

The automatic proof hashes the authority identity, generation and nonce,
stable task authority, captured input/contract/tool-ledger/projected-wire
fingerprints, selected account, and exact wire fingerprint. It contains no
request content, prompt text, anchor, credentials, or tool output.

## Failure semantics

- A successful in-place replay suppresses the original stale-anchor terminal
  event.
- A physically proven pre-send failure restores an automatically authorized
  generation to APPROVED so an exact later retry can be claimed without a
  dashboard action.
- An ambiguous post-send outcome remains UNKNOWN and is non-replayable.
- Requests outside automatic eligibility retain the existing explicit
  administrator flow and stable non-retryable HTTP 400 errors.
- A database uniqueness race at either pre-commit flush is rolled back and
  mapped to the stable automatic proof-conflict error before any upstream send.

## Rollback floor

The status endpoint reports active automatic UNKNOWN or APPROVED authorities.
While any exist, `preAutomaticRecoveryImageCompatible` is false and the minimum
rollback capability is `rowless_automatic_recovery_v3`. Older images cannot
interpret automatic authorization provenance or its preflight rollback rules.
