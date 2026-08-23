## 1. Durable authority

- [x] Add an additive migration and model fields for the content-free rejected-anchor marker.
- [x] Add fenced repository/coordinator operations to set and read the marker.
- [x] Clear the marker atomically with replacement terminal anchor registration.

## 2. Admission and failure semantics

- [x] Persist the marker on the first unproved proxy-injected stale-anchor rejection.
- [x] Reject subsequent unproved owner/anchor-bound requests before upstream connect/send.
- [x] Preserve existing verified same-owner full-resend recovery and PR #19 no-duplicate behavior.

## 3. Verification

- [x] Add durable cross-process/lease-expiry, concurrent marking, owner/anchor fencing, and clearing tests.
- [x] Add an HTTP regression with stored prefix, pending manifest, developer/user messages, and mismatched tool output.
- [x] Prove the second scheduled-style delta performs zero upstream connection or dispatch.
- [x] Prove a verified replacement terminal checkpoint clears the marker and later deltas continue from the new anchor.
- [x] Run focused unit/integration, replay-safety, migration, format, lint, type, architecture, and strict OpenSpec gates.
