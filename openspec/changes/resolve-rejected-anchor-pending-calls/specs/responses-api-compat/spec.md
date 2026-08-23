## MODIFIED Requirements

### Requirement: Responses stale-anchor recovery uses exact settled pending calls

When a durable rejected-anchor marker has a non-empty pending-call manifest,
the gateway MUST admit a complete-context recovery only if the suffix contains
a self-contained call/output settlement whose call-id-to-type maps exactly
equal that durable manifest.  Canonical response-owned reasoning MAY precede
the settlement because the existing replay projection removes it.  After the
exact settlement, the gateway MAY accept no tail, a bounded user follow-up
sequence, or one canonical retained `agent_message` followed by such a
sequence.

Missing, duplicate, call/output-order-invalid, type-drifted, malformed,
unrelated, or extra tool loops MUST fail closed. Parallel calls MUST match the
durable exact ID-to-type bijection; their insertion order is not authority. A
developer/user boundary or a different
call/output pair MUST NOT prove settlement.  A non-empty pending manifest MUST
NOT use the broader retained-output alternative.

Admission MUST retain the existing API-key-scoped logical session, same-account
owner, exact stored-prefix, complete-input fingerprint, marker wire
compare-and-set, and durable recovery-attempt journal checks.  The gateway MUST
send at most one unanchored replacement wire.  An ambiguous or failed attempt
MUST retain the marker and MUST NOT be reclaimed.  A successful terminal
checkpoint MUST publish the new anchor against the original complete input and
clear the marker atomically through the existing terminal transaction.

Ordinary startup, closed-row, and abandoned-row cleanup MUST NOT delete a row
while it carries a rejected-anchor marker.  Once terminal publication clears
the marker, normal retention MAY delete the row.

#### Scenario: exact settlement followed by bounded user input recovers once

- **GIVEN** the durable manifest contains pending `call_A`
- **AND** the exact complete resend contains canonical reasoning, `call_A`, its
  matching output, and bounded later user input
- **WHEN** stale-anchor recovery runs
- **THEN** the gateway dispatches one same-account unanchored recovery
- **AND** does not re-execute `call_A`
- **AND** terminal completion publishes the replacement checkpoint once

#### Scenario: exact settlement followed by inter-agent output recovers once

- **GIVEN** the exact complete resend settles every durable pending call
- **AND** one canonical retained `agent_message` and bounded user input follow
- **WHEN** stale-anchor recovery runs
- **THEN** the response-owned bookkeeping is projected safely
- **AND** the complete client context remains bound to the terminal checkpoint

#### Scenario: unrelated or additional calls remain blocked

- **GIVEN** the durable manifest contains pending `call_A`
- **WHEN** the suffix contains `call_B`, a missing or wrong output, a type or
  call/output ordering drift, a second tool loop, or only developer/user input
- **THEN** the gateway rejects before upstream connect or send
- **AND** retains the marker and pending manifest

#### Scenario: ambiguous recovery is not replayed

- **GIVEN** an admitted recovery may have reached upstream and its journal is
  `UNKNOWN`
- **WHEN** an identical or different wire arrives after restart or lease expiry
- **THEN** the gateway does not reclaim or redispatch it
- **AND** the historical irreversible effect count remains unchanged

#### Scenario: cleanup preserves recovery authority

- **GIVEN** a marker-bearing row is closed, ownerless, lease-expired, and older
  than ordinary retention cutoffs
- **WHEN** startup, closed-row, or abandoned-row cleanup runs
- **THEN** the row, marker, pending manifest, and journal authority remain
- **AND** normal purge eligibility resumes only after marker clear
