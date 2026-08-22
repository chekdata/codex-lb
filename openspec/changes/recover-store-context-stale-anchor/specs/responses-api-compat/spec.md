## ADDED Requirements

### Requirement: Exact store-context trims can recover a rejected proxy anchor in place

When the HTTP Responses bridge receives a complete unanchored request, proves
that its input prefix exactly matches the current live session checkpoint, and
then trims that prefix before injecting the live session's
`previous_response_id`, it MUST retain an immutable request-local completeness
proof. If upstream explicitly rejects that proxy-injected anchor before any
response event, the bridge MAY replay the retained complete request exactly
once without the anchor on the same account. The proof MUST bind the logical
session key, account, rejected response anchor, stored input count and
fingerprint, pending tool-call manifest, and complete request fingerprint. The
existing durable checkpoint MUST remain authoritative until the replacement
request completes.

#### Scenario: request-local proof recovers an exact-prefix trim

- **GIVEN** a live bridge completed a response and stored its exact input prefix
- **AND** the next unanchored client request contains that prefix, the prior response output or exact pending tool-call completion, and new input
- **AND** the bridge verifies the prefix, seals the complete request proof, trims the prefix, and injects its response anchor
- **WHEN** upstream rejects that anchor before emitting any response event
- **THEN** the bridge opens one replacement WebSocket on the same account
- **AND** sends the original complete request exactly once without `previous_response_id`
- **AND** a completed replacement atomically supersedes the old checkpoint

#### Scenario: incomplete or changed request remains fail closed

- **GIVEN** the stored prefix does not match, prior output or pending tool context is missing, any proof-bound session or request field changed, the anchor was client supplied, or an upstream response event was already observed
- **WHEN** stale-anchor recovery is evaluated
- **THEN** the bridge does not use request-local unanchored replay
- **AND** it preserves the existing durable checkpoint and established fail-closed error behavior

#### Scenario: recovery remains single-dispatch and account-bound

- **GIVEN** a request-local proof authorizes stale-anchor recovery
- **WHEN** the replacement dispatch is attempted
- **THEN** it is attempted at most once on the same owning account
- **AND** an ambiguous send or replacement failure does not authorize another replay or an account change
