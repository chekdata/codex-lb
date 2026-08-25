# Tasks

- [x] Add authorization provenance columns and migration/backfill coverage.
- [x] Add repository CAS for automatic live capture, unsent generation replacement, and preflight claim.
- [x] Replay an eligible first stale-anchor rejection in the same downstream request.
- [x] Replace an eligible unsent legacy generation before account selection.
- [x] Preserve replacement journal, send-marker, rollback, UNKNOWN, and completion invariants.
- [x] Add unit, integration, concurrency, cancellation, ambiguous-send, and migration tests.
- [x] Require retained completed output before a fresh user follow-up and keep
  incremental-only requests on the operator path.
- [x] Admit only canonical response-owned failed root-turn retry chains while
  rejecting assistant output, tool activity, unknown items, and malformed
  user/developer ordering in the retry tail.
- [x] Expose the automatic-authority rollback floor and map all uniqueness
  flush races to a stable fail-closed rejection.
- [x] Validate OpenSpec, lint, type checks, and focused/full test gates.
- [ ] Release to production and recover the three original tasks serially.
