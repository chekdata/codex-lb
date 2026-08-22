## Why

The HTTP Responses bridge can receive a complete conversation input, verify
that its prefix exactly matches the live session checkpoint, trim that prefix,
and inject the checkpoint's `previous_response_id`. If upstream then rejects
that proxy-injected anchor before producing any response event, the current
recovery gate recognizes only durable full-resend proof. A live-session-only
request therefore returns `previous_response_anchor_unrecoverable` even though
the bridge still holds the exact complete request it verified and trimmed.
Repeated client retries carry the same stale continuity state and can loop.

## What Changes

- Mint an immutable request-local proof only when the bridge itself verifies
  and applies an exact stored-prefix trim to a complete-context request.
- Bind the proof to the logical session key, account, response anchor, stored
  prefix fingerprint, pending tool-call manifest, and full request fingerprint.
- Permit one same-request, same-account, unanchored replay only when upstream
  rejects the proxy-injected anchor before any response event and the proof
  still matches the live session.
- Keep incomplete inputs, client-owned anchors, post-event failures, account
  changes, and repeated recovery attempts fail closed.
- Preserve the old durable checkpoint until the replacement response completes
  and publishes its successor.
