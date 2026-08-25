# Delta: Responses API compatibility

## ADDED Requirements

### Requirement: Proofed official live turns recover without employee approval

The service MUST automatically rebase the same live downstream turn when an
official root Codex turn supplies valid, mutually consistent turn metadata and
an eventless stale-anchor rejection occurs, but only when the request is
self-contained, account-neutral, has zero unresolved direct calls, and can be
fenced by the existing durable at-most-once authority. The request MUST retain
prior completed assistant or agent output before its fresh user follow-up. The
employee MUST NOT need to visit the dashboard or retry the turn.

#### Scenario: First stale rejection recovers in place

- **GIVEN** an official root turn with a complete safe resend and a stale anchor
- **WHEN** upstream rejects the anchor before any response event
- **THEN** the service persists and claims one automatic recovery generation
- **AND** reconnects on the same account with the exact anchor-free projection
- **AND** binds the replacement journal and send marker before physical send
- **AND** returns the recovered response events on the original downstream turn
- **AND** does not emit an administrator-approval or reconnectable error.

#### Scenario: Unsent old generation is replaced safely

- **GIVEN** a CAPTURED or APPROVED authority with no dispatch request,
  replacement binding, send marker, journal, or marker-attempt claim
- **AND** a newer eligible complete request for the same root task, anchor,
  account, scope, and marker
- **WHEN** the complete request no longer matches the old input fingerprint
- **THEN** the service records a content-free supersession audit
- **AND** increments the generation and atomically claims the new exact wire
- **AND** the old generation cannot dispatch afterward.

#### Scenario: Ambiguous and unsafe requests remain fail closed

- **GIVEN** an UNKNOWN or CONSUMED authority, a child task, conflicting
  identity metadata, an unresolved tool call, incomplete history, changed
  account, changed wire, or a dispatch that may have started
- **WHEN** automatic recovery is considered
- **THEN** the service MUST NOT replace, approve, or replay that generation
- **AND** MUST preserve the existing stable fail-closed error and durable fence.

#### Scenario: Incremental-only input keeps the operator gate

- **GIVEN** an official root turn whose request contains a new user message but
  does not retain any prior completed assistant or agent output
- **WHEN** upstream rejects its stale anchor before any response event
- **THEN** the service MUST NOT automatically claim or replay the request
- **AND** MUST preserve the operator-authorized semantic-rebase flow.

#### Scenario: Canonical failed root-turn retries remain recoverable

- **GIVEN** an official root turn retains a completed assistant final answer
- **AND** subsequent failed retries contain only response-owned user and
  developer messages with unique valid message identifiers
- **AND** the retry chain contains at least two user messages, starts and ends
  with user input, and has no consecutive developer messages
- **WHEN** the next live retry independently satisfies every automatic
  recovery identity, settlement, account-neutrality, and at-most-once gate
- **THEN** the service MAY supersede a physically unsent CAPTURED or APPROVED
  generation and claim the exact new request automatically
- **BUT** any assistant output, tool activity, unknown item, duplicate message
  identifier, leading developer message, or trailing developer message in the
  retry tail MUST keep the request fail closed.

#### Scenario: Settled post-answer tool history does not strand a failed follow-up retry

- **GIVEN** an official root turn retains a completed assistant final answer
- **AND** its trailing history contains canonical response-owned reasoning and
  one or more adjacent self-contained `custom_tool_call` and matching output
  pairs with unique call IDs and no unresolved call
- **AND** at most one bounded account-neutral developer refresh precedes at
  least two response-owned user retry messages
- **WHEN** the live retry independently satisfies every automatic recovery
  identity, settlement, account-neutrality, and at-most-once gate
- **THEN** the service MAY supersede a physically unsent CAPTURED or APPROVED
  generation and claim the exact anchor-free request once
- **BUT** a missing or reordered output, mismatched or duplicate call ID,
  unknown tool type, malformed reasoning, second developer message, or fewer
  than two retry messages MUST keep the request fail closed.

#### Scenario: Marker-backed anchorless retry uses the verified stale anchor

- **GIVEN** an eligible official root turn omits `previous_response_id`
- **AND** a hard-session durable marker verifies the task, account, and stale
  anchor before the gateway injects that anchor for reattach
- **WHEN** a physically unsent CAPTURED or APPROVED authority is superseded by
  the live request
- **THEN** automatic preflight MUST bind the marker's verified stale anchor
- **AND** the exact claimed anchor-free projection MUST reach upstream once
- **AND** later store-context processing MUST NOT trim or rebuild that wire
- **AND** `response.completed` MUST consume the authority and clear the marker.

#### Scenario: Active automatic authority raises the rollback floor

- **GIVEN** at least one automatically authorized APPROVED or UNKNOWN authority
- **WHEN** an operator reads the rowless recovery status
- **THEN** `preAutomaticRecoveryImageCompatible` MUST be false
- **AND** the minimum rollback capability MUST be
  `rowless_automatic_recovery_v3`.

#### Scenario: Automatic completion publishes ordinary continuity

- **GIVEN** one automatic live rebase reached its send primitive
- **WHEN** upstream publishes `response.completed`
- **THEN** the response anchor, complete input fingerprint, aliases, recovery
  journal, and CONSUMED authority MUST commit atomically
- **AND** the next ordinary follow-up MUST use that new anchor.
