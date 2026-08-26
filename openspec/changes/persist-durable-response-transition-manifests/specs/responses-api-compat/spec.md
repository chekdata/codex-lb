## ADDED Requirements

### Requirement: Durable completions record a content-free response transition manifest

For every eligible HTTP Responses bridge `response.completed`, the service MUST
atomically persist a versioned content-free manifest of the ordered normalized
response output together with the durable input checkpoint and pending
tool-call manifest. The manifest MUST bind its canonicalization version,
terminal status, ordered item kinds and fingerprints, bounded response-owned
identity hashes, and pending-call digest. It MUST NOT persist prompt text,
response text, reasoning, encrypted content, tool arguments, tool output,
credentials, headers, or request bodies. Unsupported or inconsistent terminal
output MUST produce no manifest rather than a partial manifest.

#### Scenario: eligible completion records durable proof

- **WHEN** a bridge response completes with internally consistent output events and terminal output
- **THEN** its durable session and active recovery marker store the same transition manifest atomically with the response anchor and input checkpoint
- **AND** the stored document contains no conversation or tool content

#### Scenario: unsupported output remains legacy

- **WHEN** a completion contains an unsupported item type or inconsistent output lifecycle
- **THEN** the response still completes normally
- **AND** its durable transition manifest is null
- **AND** later stale-anchor recovery does not infer a partial manifest

### Requirement: Manifest-backed full resends recover stale proxy anchors without layout allowlists

When a complete-context request exactly matches a durable input checkpoint and
then contains the response-owned output recorded by its transition manifest,
the service MUST validate the transition by manifest and call-ledger state
rather than by a fixed positional retry layout. It MUST require every durable
pending call to have exactly one matching self-contained output, group later
canonical user/developer inputs by response-owned task and turn identity,
require at least one fresh user turn, and reject any uncovered assistant/tool
output, unresolved, orphaned, duplicate, type-mismatched, account-scoped, or
identity-conflicting item.

If upstream rejects the proxy-injected anchor before any response event, a
matching request MAY be replayed exactly once without that anchor on the same
account. The proof MUST bind the session, task, account, rejected anchor, stored
prefix, transition-manifest digest, pending-call manifest, complete request, and
actual wire fingerprint.

#### Scenario: commentary and pending call settle through the recorded manifest

- **GIVEN** a durable checkpoint recorded reasoning, assistant commentary, and a pending custom-tool call in its transition manifest
- **AND** a complete resend exactly matches the stored prefix and manifest, supplies the matching tool output, and contains canonical later retry turns
- **WHEN** upstream rejects the proxy-injected anchor before any response event
- **THEN** the service sends the sealed anchor-free request once on the same account
- **AND** the client receives the replacement response stream instead of an administrator-approval loop

#### Scenario: repeated developer and user items are identity-grouped

- **GIVEN** official Codex hoists multiple canonical developer messages ahead of their user messages
- **AND** every item has a unique bounded response-owned identity that groups into a task-matching retry turn
- **WHEN** manifest-backed transition verification runs
- **THEN** eligibility is determined from identity and ledger state rather than a fixed developer/user positional count

#### Scenario: one-item drift fails closed

- **GIVEN** a complete resend changes, omits, duplicates, or reorders a manifest-bound output item or pending-call settlement
- **WHEN** stale-anchor recovery is evaluated
- **THEN** the service performs no unanchored send
- **AND** returns the existing stable operator-gated recovery result without exposing request content

### Requirement: Legacy checkpoints do not broaden rowless automatic recovery

A checkpoint with no supported transition manifest MUST retain the existing
operator-gated recovery behavior whenever exact durable proof cannot be made.
This capability MUST NOT add a new positional rowless allowlist as a substitute
for missing manifest evidence.

#### Scenario: pre-migration checkpoint requires operator evidence

- **GIVEN** a durable checkpoint predates transition manifests
- **WHEN** its proxy anchor is rejected and existing exact durable proof is insufficient
- **THEN** automatic manifest recovery is unavailable
- **AND** the service preserves the existing rowless authority and exactly-once operator gate
