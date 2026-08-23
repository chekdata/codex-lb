# Delta: Responses API compatibility

## ADDED Requirements

### Requirement: Rowless stale-anchor recovery is an explicit semantic rebase

When upstream explicitly rejects a client-supplied `previous_response_id`
before emitting any response event and the durable session checkpoint needed
for automatic proof is absent, the service MUST persist a content-free,
non-cascading recovery authority and MUST return a stable non-retryable
authorization-required error.  It MUST NOT retry the request unanchored, infer
authority from logs, or create a different logical thread.

#### Scenario: Capture survives session cleanup

- **GIVEN** a hard same-thread request with an explicit stale anchor
- **AND** upstream rejects it with zero response events
- **WHEN** the associated bridge session is absent or later purged
- **THEN** a tombstone keyed by API-key scope, stable task-authority hash, and anchor
  hash remains
- **AND** it contains no request content or plaintext anchor
- **AND** the same contract is rejected locally before account selection or
  WebSocket connect until an administrator approves its exact generation.

#### Scenario: Only a dashboard admin can approve

- **GIVEN** a captured generation
- **WHEN** a proxy bearer, dashboard guest, stale challenge, mismatched
  generation, or malformed client-owned receipt attempts approval
- **THEN** no state changes
- **AND** only an authenticated trusted-proxy dashboard operator with the exact challenge and
  `operator_acknowledged_semantic_rebase` acknowledgement can approve it.

#### Scenario: Receipt and request are exact

- **GIVEN** an approved generation bound to a client-owned checkpoint receipt
- **WHEN** the next same-thread request arrives
- **THEN** its complete captured input, non-input contract, ordered direct-call
  ledger, account intent, task/session identity binding, and zero unresolved
  calls MUST match
- **AND** this recovery generation MUST reject every suffix or input mutation
- **AND** the versioned projected logical payload and exact post-transform
  serialized wire MUST match the captured challenge before dispatch
- **AND** a late account-metadata or other wire drift MUST stop before physical
  send and may restore approval only with exact proven-unsent authority
- **AND** later ordinary follow-ups MUST wait for the replacement anchor
- **AND** the projected anchor-free request MUST be self-contained and account
  neutral; account-scoped requests are never eligible.

#### Scenario: One at-most-once dispatch

- **GIVEN** an approved exact request
- **WHEN** concurrent workers attempt it
- **THEN** exactly one worker transitions the non-cascading authority to
  UNKNOWN before account selection or WebSocket connect
- **AND** the winner binds the replacement-session journal before physical send
- **AND** a physically proven pre-send failure may restore APPROVED
- **BUT** an ambiguous post-send result remains UNKNOWN and is never replayed.

#### Scenario: Completion publishes a normal checkpoint

- **GIVEN** the one semantic-rebase dispatch reaches `response.completed`
- **WHEN** terminal durable settlement commits
- **THEN** the replacement bridge session, aliases, new response anchor, full
  client input count/fingerprint, journal settlement, and CONSUMED authority
  are published atomically
- **AND** later requests use ordinary continuity rather than the tombstone.

#### Scenario: Stable client and retention behavior

- **GIVEN** an authorization-required, recovery-marker, UNKNOWN, or already
  CONSUMED rowless state
- **WHEN** the signed Desktop repeats the same contract
- **THEN** the service returns HTTP 400 with a stable machine code/action and
  does not enter the generic reconnect/replay path
- **AND** omitting or changing the anchor cannot bypass an exact stable-task
  tombstone match for the captured request domain
- **AND** a unique exact APPROVED match still uses the same one-generation CAS,
  while a genuinely changed turn must use ordinary replacement-anchor continuity
- **AND** expired unapproved CAPTURED rows may be purged after seven days
- **BUT** APPROVED, UNKNOWN, and CONSUMED identity fences are never deleted by
  automatic bridge cleanup.
- **AND** any non-CAPTURED fence makes a pre-rowless application image an
  invalid rollback target, exposed through the trusted-operator status API.
