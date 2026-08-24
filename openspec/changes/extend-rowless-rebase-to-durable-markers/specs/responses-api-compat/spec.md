# Delta: Responses API compatibility

## ADDED Requirements

### Requirement: Durable recovery markers support an explicit administrator semantic rebase

When an active durable recovery marker cannot be recovered by exact owner-bound
automatic proof, the service MUST keep automatic safety predicates unchanged
and MAY capture one administrator-authorized semantic-rebase authority for the
same durable marker generation.

#### Scenario: Automatic exact proof remains preferred

- **GIVEN** an active durable recovery marker and a complete resend that exactly
  settles its stored prefix and pending manifest
- **AND** no earlier rowless UNKNOWN or CONSUMED tombstone fences the same task turn
- **WHEN** the request is evaluated
- **THEN** the existing automatic proof path claims the marker generation
- **AND** no administrator authority is required or dispatched.

#### Scenario: Automatic proof preserves earlier rowless no-replay tombstones

- **GIVEN** an active durable recovery marker and an exact automatic full-resend proof
- **AND** a pre-marker or different-marker rowless UNKNOWN or CONSUMED authority
  already fences the same task turn
- **WHEN** the automatic request is evaluated
- **THEN** it terminates locally before account selection, WebSocket connect or send
- **AND** the lookup remains enforced when `thread-id` or other dispatch-only
  routing identity is missing but API scope and rejected anchor still identify the fence
- **AND** the earlier no-replay authority is not superseded by the current marker claim
- **AND** only CAPTURED or not-yet-dispatched APPROVED authority bound to the exact
  current marker may be superseded by the automatic proof.

#### Scenario: Mismatched pending evidence captures without upstream work

- **GIVEN** the saved anchor was rejected before any response event
- **AND** the current complete request is self-contained and account neutral
- **BUT** its call/output IDs do not exactly settle the durable pending manifest
- **WHEN** automatic proof fails
- **THEN** the service persists one authority bound to the exact durable session,
  API scope, account, anchor generation, task identity and request contract
- **AND** returns the stable authorization-required action
- **AND** performs no additional account selection, WebSocket connect or upstream send.

#### Scenario: A resolved root-task turn-state alias can use the durable marker

- **GIVEN** a normal Codex root-task resend carries a fresh per-turn
  `x-codex-turn-state` affinity value
- **AND** durable lookup resolves that alias to the exact hard session-header,
  account and active recovery marker
- **AND** the stable session ID and prompt-cache key equal the root task identity
  supplied by `thread-id` or, when that header is omitted, by
  `x-client-request-id`
- **AND** when both identity headers are present they agree, without a
  conflicting session alias
- **AND** any metadata thread identity agrees and no header/body parent or
  subagent signal is present
- **WHEN** automatic pending-manifest proof fails but the complete request is
  otherwise eligible for marker-backed administrator recovery
- **THEN** the service captures or consumes the one marker-bound authority
  instead of rejecting the request solely because turn-state affinity is present
- **AND** no-row recovery, child-thread recovery, unresolved aliases and task-ID
  drift remain fail closed.

#### Scenario: Client request identity cannot promote a child task

- **GIVEN** a child Codex task shares its root session ID and prompt-cache key
- **AND** `thread-id` is omitted but `x-client-request-id` identifies the child
- **WHEN** the child submits a marker-backed or missing-row recovery request
- **THEN** the request remains ineligible because its client request identity
  differs from the root session and prompt-cache key
- **AND** no authority, account selection, WebSocket connect or send occurs.

#### Scenario: Metadata is corroboration, not replacement authority

- **GIVEN** `thread-id` is omitted and the client request ID otherwise matches
  the root session and prompt-cache key
- **WHEN** client metadata names a different thread or contains a parent/subagent
  signal
- **THEN** marker-backed administrator recovery remains locally fail closed
- **AND** direct-header and body-nested turn metadata are checked independently
  so conflicts, malformed values, oversized values and non-turn request kinds
  cannot be hidden by metadata merge precedence
- **AND** metadata alone can never create a task authority.

#### Scenario: Official Responses-Lite 0.149 schema remains stateless

- **GIVEN** a complete root-task resend uses the official Responses-Lite
  `additional_tools` developer prefix
- **AND** its tools contain only a closed namespace of validated
  function/custom declarations plus an optional client-executed tool-search
  declaration
- **AND** client metadata session and thread IDs exactly equal the already
  verified session, prompt-cache and task identity while turn ID is nonblank
- **AND** canonical nested turn metadata contains the same complete identity,
  uses the closed Codex 0.149 turn shape and agrees across body and direct-header
  carriers
- **AND** an optional product-owned `workspace_kind` is a nonblank string of at
  most 128 bytes and has the same value in both carriers
- **AND** a present Responses-Lite reasoning context is exactly `all_turns`
- **AND** any flat root-turn, installation or window projection equals the
  canonical nested value
- **AND** a bounded body-only tool namespace inventory may exceed the 16 KiB
  compatibility-header limit while a matching direct carrier omits that inventory
- **WHEN** account-neutral replay eligibility is evaluated
- **THEN** those schema and identity fields do not cause a false rejection
- **AND** no-row and marker-backed recovery retain the same self-contained,
  settled-ledger and account-neutral authority gates.

#### Scenario: Responses-Lite schema drift remains fail closed

- **GIVEN** a request contains an empty or nested namespace, a namespace child
  missing an official required field, an unknown tool field, a non-client or
  malformed tool-search schema, a null or non-grammar namespace custom format,
  a deferred flag other than omitted or literal
  `true`, missing or drifting metadata identity, conflicting body/direct
  metadata carriers, a direct carrier containing body-only tool inventory,
  drifting flat root-turn/installation/window projection, parent/subagent
  lineage, an empty, oversized or drifting `workspace_kind`, an unknown nested
  metadata field, a null, blank, case-variant, `last_turn`, structured or
  otherwise non-canonical reasoning context,
  a conversation or prompt reference, or file/container/vector-store state
- **WHEN** recovery eligibility is evaluated
- **THEN** the request remains non-neutral and cannot create or consume a
  semantic-rebase authority
- **AND** no account selection, WebSocket connect or upstream send occurs.

#### Scenario: Invalid recovery metadata stops before routing

- **GIVEN** a stale-anchor request supplies a verified root session, prompt-cache
  key and thread identity
- **AND** at least one direct-header or body turn-metadata carrier is present
- **BUT** a carrier is malformed, oversized for its carrier kind or conflicts
  with the other carrier
- **WHEN** no durable recovery authority has already resolved that anchor
- **THEN** the service returns a stable local invalid-metadata response
- **AND** creates no authority and performs no account selection, WebSocket
  connect or upstream send
- **AND** a legacy request with no metadata carrier is not rejected by this gate.

#### Scenario: Rowless projection removes only semantics-free Codex transport artifacts

- **GIVEN** a complete client checkpoint whose direct call ledger is fully settled
- **AND** a tool output contains one exact empty `input_text` transport tail
- **AND** a canonical response-owned agent delivery contains one readable `input_text`
  followed by one opaque `encrypted_content` transport sibling
- **WHEN** the marker-backed rowless authority is captured
- **THEN** the original item count and full-input fingerprint bind the unchanged client request
- **AND** the separately fingerprinted rowless projection removes only those exact
  semantics-free parts while retaining function namespaces and all non-empty output
- **AND** reordered parts, extra fields, missing readable delivery, or any non-canonical
  variant remains fail closed before account selection or upstream send.

#### Scenario: Administrator and automatic claims are mutually exclusive

- **GIVEN** one approved administrator authority for an active marker
- **WHEN** administrator and automatic recovery attempts race with even the same
  raw wire fingerprint
- **THEN** both lock the same marker generation
- **AND** the administrator claim uses its domain-separated authority/generation digest
- **AND** exactly one path wins before account selection or connect
- **AND** the loser cannot create a second journal or upstream effect.

#### Scenario: Proven-unsent failures restore the marker generation

- **GIVEN** administrator preflight has claimed the marker generation
- **WHEN** local setup fails before replacement binding, the request owner is
  cancelled before invoking its initial send helper, a typed first socket close
  is followed by cancellation before the fresh send helper, or every attempted
  socket is physically proven closed before its send primitive
- **THEN** one transaction restores APPROVED and clears the administrator marker claim
- **AND** deletes any exact UNKNOWN journal and replacement binding when present
- **AND** retains the old anchor, account and recovery-required marker.

#### Scenario: Socket-only reconnect is not a proven-unsent send

- **GIVEN** an administrator request reconnects a closed socket without sending
- **AND** the later initial send primitive may deliver the request
- **WHEN** that send is cancelled before any response event is observed
- **THEN** the generic reconnect counter MUST NOT authorize rollback
- **AND** authority, journal and marker claim remain UNKNOWN
- **AND** a later retry cannot produce a second upstream effect.

#### Scenario: Ambiguous delivery remains permanently fenced

- **GIVEN** the send primitive may have delivered the administrator request
- **WHEN** the stream fails without a physical unsent proof
- **THEN** authority and journal remain UNKNOWN
- **AND** the marker attempt claim and old anchor remain
- **AND** no automatic, reconnect or administrator replay is permitted.

#### Scenario: Terminal completion atomically establishes the new checkpoint

- **GIVEN** the one administrator semantic-rebase request reaches `response.completed`
- **WHEN** durable settlement commits
- **THEN** the new anchor, alias, complete client checkpoint, REPLAYED journal and
  CONSUMED authority become visible together
- **AND** the recovery marker and marker attempt claim are cleared together
- **AND** any persistence failure leaves the old generation fail-closed.

#### Scenario: Automatic terminal completion is also one durable transaction

- **GIVEN** the automatic exact-proof path has claimed and dispatched one marker generation
- **WHEN** it reaches `response.completed` with a complete, supported pending
  tool-call manifest
- **THEN** the new anchor, alias, complete client checkpoint and REPLAYED journal
  become visible in the same transaction that clears every marker field
- **AND** an absent, malformed or unsupported terminal tool-call manifest is
  returned as a persistence error without clearing the marker or settling the journal
- **AND** a transaction failure is returned as a terminal persistence error before
  downstream success is delivered
- **AND** the old anchor, marker claim and UNKNOWN journal remain intact.

#### Scenario: Schema and rollback capability preserve replay fences

- **GIVEN** the rowless authority table exists
- **WHEN** its origin-marker column is absent
- **THEN** durable bridge startup readiness fails
- **AND** any marker-bound authority, including CAPTURED, requires
  `rowless_marker_recovery_v2`
- **AND** migration downgrade and v1-only image rollback are rejected while such
  authority exists.
