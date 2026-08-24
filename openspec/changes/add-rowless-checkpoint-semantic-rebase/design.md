# Design: operator-acknowledged rowless semantic rebase

## Authority record

`http_bridge_rowless_recovery_authorities` is deliberately independent of
`http_bridge_sessions`; it has no cascading foreign key.  Its stable identity
is `(api_key_scope, strong_session_hash, stale_anchor_hash)`.  The strong
session hash is derived from a task-authority digest over exact ingress
`session-id`, body `prompt_cache_key`, and `thread-id`; it is independent of
ephemeral turn-state routing.  Plain session and response identifiers are
never persisted.

This release admits only root task requests for which the three identity
values are exactly equal.  Spawned child threads can share their root's
session/prompt bridge row, so they fail closed until the durable bridge has a
per-thread replacement-session identity.

State transitions are monotonic except for a physically proven unsent rollback:

```
captured -> approved -> unknown -> consumed
                         |
                         +-- proven unsent only --> approved
```

`unknown` means the send primitive may have been reached and is never replayed.
Consumed rows are permanent compact no-replay tombstones.  Automatic cleanup
may purge only unapproved CAPTURED rows older than seven days; it never purges
APPROVED, UNKNOWN, or CONSUMED authority.
Schema downgrade likewise refuses to drop the authority table while any
APPROVED, UNKNOWN, or CONSUMED replay fence exists.  Rolling back an image does
not require erasing these monotonic database fences.
The trusted-operator status endpoint publishes content-free per-state counts
and an explicit minimum rollback capability.  After the first non-CAPTURED
authority exists, production MUST NOT roll back to a pre-rowless image because
such an image cannot enforce the retained replay fences.

Tombstone lookup does not trust the retry's anchor field.  For the stable root
task identity, an exact match of captured input count/fingerprint, non-input
contract, retained direct-call ledger, and projected logical payload resolves
one existing authority even when the client omits or changes
`previous_response_id`.  CAPTURED, UNKNOWN, and CONSUMED therefore remain local
terminal states; a unique APPROVED match still passes through the same
generation CAS.  A changed/new turn does not match this request domain and is
admitted only through ordinary continuity with the replacement anchor.
The database enforces that semantic-turn identity as a second unique fence in
addition to the direct stale-anchor lookup key, so two different rejected
anchors cannot create or approve two generations for the same task turn.

## Capture

Capture is currently allowed only for a canonical hard `session_header` key,
the exact official `session-id`/`thread-id` headers, an explicit body
`prompt_cache_key`, an explicit client-provided anchor, the canonical
stale-anchor error, and `response_events_seen=0`.  The server stores:

- raw client input count and canonical SHA-256 fingerprint;
- canonical non-input request-contract SHA-256 fingerprint;
- an ordered content-free ledger digest over direct call/output id, type, and
  status, plus unresolved count;
- a versioned digest of the projected anchor-free logical payload and a
  separate digest of the exact serialized bytes at the first send boundary;
- selected account intent;
- whether an anchor-free projected request is self-contained and account
  neutral;
- generation, random nonce, timestamps, and the stable task-authority digest.

The response is a stable non-retryable
`previous_response_recovery_authorization_required` error.  An existing record
is never overwritten by a different contract.

## Admin acknowledgement

List/challenge/approve routes require a nonblank operator identity supplied by
the configured trusted-proxy dashboard mode; DISABLED, standard password,
proxy bearer, spoofed/untrusted header, and guest identities cannot authorize
recovery.  An approval request
must present the exact generation and one-time challenge plus the literal
acknowledgement `operator_acknowledged_semantic_rebase`.

The server canonicalizes the client-owned, content-free receipt and verifies
its declared SHA-256.  The receipt binds remote session JSONL SHA-256, byte
size, exact last read offset, task and session identity, stable
task-authority/strong-session hashes, a full-checkpoint ledger digest, and zero
unresolved calls.  The full-checkpoint ledger is a distinct domain from the
compacted request-input direct-call ledger; equality between those two digests
is neither expected nor used.  Approval records only receipt and identity
digests, never the JSONL or conversation content.

The receipt contains two explicitly different evidence domains.  The JSONL
SHA/size/offset, task identity, full-checkpoint ledger, and composite
`unresolved_count=0` are independent client checkpoint evidence.  The four
captured-request count/input/contract/retained-ledger values carry literal
`captured_request_binding_provenance=server_challenge`: the trusted operator
copies and explicitly acknowledges the content-free server challenge, and the
transaction compares it back to the unchanged captured row.  These four
values are not described as independently client-verified physical evidence.
Two additional server-challenge values bind the versioned projected logical
payload and the exact anchor-free projection of the first-attempt
post-installation-metadata wire bytes.  Approval surviving a deploy cannot
dispatch if projection semantics or any forwarded field changed since
capture.  Transformable external image URLs are not eligible for rowless
recovery because refetching could change their bytes.
A future transport-time client receipt may strengthen that provenance without
weakening this operator-acknowledged flow.

The client full-checkpoint ledger uses domain
`qk-client-full-checkpoint-tool-ledger-v1\0`.  From the complete-newline JSONL
prefix it selects ordered `response_item` call/output records, requires an
exact call/output type pairing and globally unique correlation IDs, and emits
only line/ordinal/kind/type plus domain hashes of the correlation ID, canonical
tool identity `{type,name,namespace}`, canonical arguments/input, canonical
output, and canonical payload without the raw ID.  Canonical JSON uses sorted
keys, ASCII escaping, and compact separators.  The overall digest hashes
`{schema:"qk_client_full_checkpoint_tool_ledger_v1",entries:[...]}` under that
domain.  `unresolved_count` is the sum of pending calls, missing-ID events,
orphan outputs, duplicate call IDs, duplicate outputs, and type mismatches;
approval requires zero.  Other JSONL records remain physically bound by the
file SHA/size/offset even when excluded from the ledger.

## Dispatch and terminal settlement

An approved request must be the exact failed turn: its complete input count and
fingerprint, non-input contract, and direct-call ledger remain exact.  This
generation permits no suffix.  A later ordinary user/developer follow-up is
admitted only after `response.completed` publishes the replacement anchor.
This avoids inventing an ordered-prefix proof from a single whole-input hash.
The anchor-free projected body must remain self-contained.  Account selection
is pinned to the captured account.

Before account lookup or WebSocket connection, one primary CAS transitions the
non-cascading authority from APPROVED to UNKNOWN with exact generation,
request, wire, and task-authority binding.  Concurrent losers stop before
selection/connect.  The winner then creates the replacement durable session
and binds its existing FK-backed UNKNOWN journal before physical send.  A
crash between stages remains UNKNOWN.  A locally observed setup failure before
any possible send, or two typed closed-before-send socket results, may restore
APPROVED after an exact journal delete; a zero-event disconnect never does.
Immediately after the final image/materialization and account-installation
metadata transform, but before journal binding or send, the winner hashes the
actual serialized wire again.  A mismatch sends zero bytes and restores
APPROVED only through the exact proven-unsent preflight rollback; an ambiguous
rollback outcome remains UNKNOWN.  This second comparison closes the account
metadata read/selection TOCTOU window.

On `response.completed`, the same terminal persistence transaction must publish
the ordinary response anchor, aliases, complete client input count/fingerprint,
and mark the authority CONSUMED.  A terminal failure consumes the dispatch but
does not authorize another attempt.  This leaves PR #19's ambiguous receive
fence intact.

## Operational limitation

The service uses OpenAI-compatible HTTP 400 invalid-request responses, not
generic 409, for every stable no-progress continuity state.  An
authorization-required response carries
`action=retry_same_turn_after_admin_approval`.  The operator must regenerate
the receipt after that failed turn, approve through the trusted-proxy API, and
use the client's same-turn Retry action; a new user/developer item is rejected.
