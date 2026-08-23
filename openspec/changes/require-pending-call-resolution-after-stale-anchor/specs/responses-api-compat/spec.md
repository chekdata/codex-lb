## ADDED Requirements

### Requirement: Rejected durable anchors require local pending-call resolution

When upstream rejects a proxy-injected `previous_response_id` before any
response event and the request does not satisfy an existing exact owner-bound
complete-context proof, the gateway MUST atomically persist a content-free
recovery-required marker on the durable session.  The marker MUST bind the
session's API-key scope, owning account, and rejected anchor, MUST survive
process replacement and ordinary bridge lease expiry, and MUST NOT store
request or conversation content.

While that marker still matches the durable owner and latest response anchor,
the gateway MUST reject every request that lacks an existing verified
owner-bound complete-context proof before selecting an upstream account,
opening a WebSocket, or dispatching `response.create`.  The response MUST use a
stable non-retry semantic error code that states pending-call resolution is
required.  Repeating the same scheduled delta MUST NOT create a new upstream
attempt.

An existing exact stored-prefix full resend or abandoned-pending-call proof MAY
recover once without the rejected anchor on the same owning account.  The
gateway MUST retain the marker and rejected anchor if that attempt does not
reach a replacement terminal checkpoint.  Publishing the replacement response
anchor after `response.completed` MUST clear the marker in the same durable
transaction.  The marker MUST NOT authorize account migration, boundary
relaxation, generic replay, or any retry after ambiguous delivery.

#### Scenario: repeated scheduled delta is blocked before upstream dispatch

- **GIVEN** upstream rejected a proxy-injected durable anchor with no response event
- **AND** the durable row has an unresolved pending-call manifest
- **AND** the first request lacked a verified complete-context proof
- **WHEN** another scheduled delta targets the same API-key scope, owner, and anchor
- **THEN** the gateway returns `previous_response_pending_call_resolution_required`
- **AND** does not select an upstream account, connect a WebSocket, or dispatch the request

#### Scenario: marker survives owner process replacement and lease expiry

- **GIVEN** a rejected-anchor marker is durable
- **WHEN** the bridge lease expires or another worker reads the session
- **THEN** the marker remains bound to the same account and anchor
- **AND** the new worker locally rejects an unverified delta

#### Scenario: exact complete-context recovery clears the marker atomically

- **GIVEN** a durable rejected-anchor marker and pending-call manifest
- **AND** a later request satisfies the existing exact owner-bound abandoned-pending proof
- **WHEN** the same-account recovery reaches `response.completed`
- **THEN** the new response anchor and full input fingerprint are published
- **AND** the rejected-anchor marker is cleared in that same durable transaction
- **AND** a later delta continues from the new response anchor

#### Scenario: different valid recoveries share one durable dispatch generation

- **GIVEN** two concurrent requests contain different wire payloads that each satisfy an exact owner-bound recovery proof
- **WHEN** both observe the durable marker before either reaches `response.create`
- **THEN** one request atomically binds its exact wire fingerprint to the marker generation and may dispatch
- **AND** the other request fails closed before an irreversible upstream effect
- **AND** exactly one replacement anchor may be published

#### Scenario: ambiguous marker recovery is not reclaimed after process loss

- **GIVEN** a marker-authorized recovery has an `UNKNOWN` durable attempt
- **AND** its owner process disappears or its bridge lease expires before a terminal checkpoint
- **WHEN** the same verified request reaches another worker
- **THEN** the worker fails closed before upstream connect or dispatch
- **AND** it does not claim, replay, or replace the existing attempt generation

#### Scenario: owner, anchor, and exactly-once fences remain closed

- **GIVEN** a marker for one API-key scope, account, and response anchor
- **WHEN** a request targets a different owner or anchor, supplies a mismatched tool output, or follows an ambiguous receive failure
- **THEN** the marker does not authorize recovery or cross-account dispatch
- **AND** all existing fail-closed and no-duplicate contracts remain in force
