## Context

The current durable checkpoint binds a response anchor to the input item count,
input fingerprint, and pending tool-call map. This proves the history before the
response and identifies calls that require client settlement, but it does not
prove which response-owned items appeared between that stored input and a later
tool output. The verifier therefore accepts only a small set of positional
suffix layouts.

codex-lb already receives the complete terminal response output. A content-free
manifest of that output is stronger evidence than client-layout inference and
does not require retaining conversation content.

## Goals

- Make durable exact-prefix recovery independent of incidental Codex item
  ordering around reasoning, commentary, and retry metadata.
- Preserve the existing same-account, one-send, pre-response-event recovery
  boundary.
- Keep all persisted evidence free of prompt, response, reasoning, tool
  argument, and tool-output content.
- Leave legacy checkpoints and unknown item types fail closed.

## Non-goals

- Automatically approve legacy rowless authorities.
- Recover a request whose exact stored prefix, task identity, account, call
  ledger, or response manifest cannot be proven.
- Treat client-provided response metadata as a substitute for a gateway-recorded
  completion manifest.

## Manifest

The `qk_http_bridge_response_transition_manifest_v1` document contains:

- schema and canonicalization version;
- terminal response status;
- ordered normalized output item descriptors;
- for each descriptor, item kind plus a SHA-256 fingerprint of its canonical
  normalized representation;
- bounded identity hashes needed to correlate response-owned items without
  persisting raw response text or metadata payloads; and
- the pending tool-call manifest digest.

The manifest MUST NOT contain raw text, reasoning, encrypted content, tool
arguments, tool output, credentials, request headers, or request bodies. The
existing pending-call map remains authoritative for exact call/output type
matching.

Known semantics-free transport artifacts use the existing replay
normalization. Unsupported output item types or inconsistent added/done/terminal
events make the manifest unavailable rather than partially trusted.

## Verification

For a complete-context resend, the verifier:

1. matches the stored input count and fingerprint exactly;
2. matches the next ordered response-owned items against the persisted manifest;
3. proves every durable pending call has exactly one self-contained matching
   output and no unresolved, orphaned, duplicate, or type-mismatched calls;
4. groups later canonical user/developer retry metadata by bounded response-owned
   turn identity instead of relying on positional developer/user counts;
5. requires at least one fresh user turn and rejects assistant/tool output that
   is not covered by another recorded manifest; and
6. seals the full request fingerprint, manifest digest, session, task, account,
   rejected anchor, and wire fingerprint into the existing immutable recovery
   proof.

The replay remains eligible only before any upstream response event and is sent
without the rejected proxy anchor at most once on the same account. Ambiguous
send outcomes remain UNKNOWN and non-replayable.

## Persistence And Compatibility

Durable sessions and recovery markers receive nullable manifest columns.
Completion updates the input checkpoint, pending-call map, and transition
manifest atomically. Marker copy/takeover paths carry the same manifest.

Rows with a null, malformed, unsupported-version, or digest-inconsistent
manifest retain current legacy behavior. They MUST NOT become automatically
eligible through a broader rowless shape rule. Rollback is refused while a
non-null v1 manifest exists unless the target image declares manifest support.

## Observability

Logs expose only schema version, item count, structural item-kind sequence,
manifest digest prefix, and a stable rejection reason. They never expose item
content or raw identities. Metrics distinguish manifest missing, malformed,
prefix mismatch, item mismatch, pending settlement mismatch, retry identity
mismatch, and successful in-place recovery.

## Testing

Tests use canonical synthetic items and sanitized golden traces. A model-based
transition suite permutes reasoning/commentary, multiple calls and outputs,
Lite/non-Lite developer placement, repeated retry turns, cancellation, stale
anchors, and duplicate/missing items. Every accepted trace must retain zero
unresolved calls and exactly one physical send; every one-item mutation outside
documented normalization must fail closed.
