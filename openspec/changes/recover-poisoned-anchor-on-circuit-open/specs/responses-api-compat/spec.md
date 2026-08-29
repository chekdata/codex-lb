# responses-api-compat Delta

## ADDED Requirements

### Requirement: Circuit-open poison quarantine makes the half-open probe unanchored

When a hard-affinity HTTP bridge retry circuit opens on an eventless
`stream_incomplete` or `stream_idle_timeout` failure, the proxy MUST quarantine
the exact affinity key with reason `retry_circuit_poisoned_anchor`. The
quarantine MUST remain active for at least the circuit's remaining cooldown plus
the half-open probe lease, and MUST be re-evaluated when a durable merge raises
the observed failure count to the circuit threshold.

While this quarantine is active, a full-conversation resend MUST NOT receive a
proxy-injected durable `previous_response_id` through fresh-reattach,
session-state, or recovery injection. The request MUST be sent upstream with
the client's complete payload and no anchor. A delta-only continuation MUST
retain the durable anchor so prior context is not lost.

An eventless upstream terminal error (`response.failed`, `response.incomplete`,
or `error`) MUST consume one attempt-scoped circuit strike when no response
event was observed. A request that already emitted response events MUST NOT
consume this pre-response strike. When the circuit threshold is reached for a
poison-class terminal error, the proxy MUST clear the durable continuity anchor
under the current owner fence; failed or fenced clears MUST leave the failure
state observable for retry.

A terminal failure that still has a verified safe full-resend body MUST NOT
consume a circuit strike: the request remains recoverable in band and the
replay claims the captured circuit generation at dispatch. A confirmed durable
anchor abandonment MUST settle the retry circuit only when every request it
covers is stranded without a safe replay; a replayable request keeps the
generation fence alive. Fenced or failed abandonment MUST leave the circuit
cooling.

#### Scenario: second eventless failure quarantines the hard key

- **GIVEN** a hard-affinity key has one eventless `stream_incomplete` failure
- **WHEN** a second eventless `stream_incomplete` failure is recorded
- **THEN** the retry circuit opens and persists its cooldown
- **AND** the key is quarantined with reason `retry_circuit_poisoned_anchor`
- **AND** a full-resend probe is planned without `previous_response_id`

#### Scenario: clean close does not poison an anchor

- **GIVEN** a hard-affinity key has two pre-response `clean_close` failures
- **WHEN** the circuit opens
- **THEN** the key is not quarantined as a poisoned anchor

#### Scenario: quarantine covers cooldown and probe lease

- **GIVEN** a poison quarantine is opened at the maximum circuit cooldown
- **WHEN** the cooldown expires and the half-open probe is admitted
- **THEN** the quarantine is still active while that probe is planned

#### Scenario: eventless terminal errors count once and clear the anchor

- **GIVEN** an anchored request receives a terminal error before any response event
- **WHEN** two such terminal failures open the circuit
- **THEN** each physical attempt contributes exactly one strike
- **AND** the durable anchor is cleared under the owner fence
- **AND** a midstream terminal error contributes no pre-response strike

#### Scenario: safe full resend does not consume a terminal strike

- **GIVEN** an anchored request has a verified complete full-resend body
- **WHEN** its upstream terminal frame rejects the stale anchor before any response event
- **THEN** the request is replayed in band without advancing the source circuit

#### Scenario: abandonment settles only stranded requests

- **GIVEN** a poison-class circuit is cooling and its durable anchor is abandoned
- **WHEN** no covered request retains a safe replay
- **THEN** the local and durable circuit state is cleared
- **BUT** a covered request with a safe replay keeps the circuit generation fence
