# Tasks

- [x] Extend the shared previous-response classifier for the exact canonical invalid-anchor message and fail closed on conflicting params or additional text.
- [x] Add unit coverage for accepted and rejected error shapes.
- [x] Add a public HTTP bridge regression that quarantines the exact production shape and recovers only after a verified complete resend.
- [x] Add a deterministic old-close/replacement-claim race regression and fence the replacement durable owner with a new epoch.
- [x] Keep same-instance epoch fencing separate from takeover permission and reject a replacement if another replica claims the lease during upstream connect.
- [x] Update the canonical Responses compatibility specification.
- [x] Run focused tests, strict OpenSpec validation, lint, and typecheck.
- [ ] Obtain terminal cloud checks and an exact-head clean Codex review before merge.
