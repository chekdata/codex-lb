## 1. Specification And Model

- [x] 1.1 Define the content-free manifest, verification contract, compatibility floor, and failure semantics.
- [x] 1.2 Add nullable durable session and recovery-marker manifest storage with migration coverage.
- [x] 1.3 Add canonical manifest build/decode/digest helpers with content-exclusion tests.

## 2. Runtime

- [x] 2.1 Capture the manifest from eligible terminal response output and persist it atomically with the checkpoint.
- [x] 2.2 Carry the manifest through lookup, takeover, marker, replacement, and completion paths.
- [x] 2.3 Verify exact-prefix resends through the manifest-backed transition state machine.
- [x] 2.4 Seal the manifest digest into request-local and durable automatic recovery proofs.
- [x] 2.5 Keep legacy or malformed manifests on the existing operator-gated path with bounded diagnostics.

## 3. Verification

- [x] 3.1 Add unit tests for canonicalization, privacy, matching, mutation rejection, and retry grouping.
- [x] 3.2 Add integration tests for stale-anchor replay, multiple tool loops, cancellation, ambiguity, and legacy fallback.
- [x] 3.3 Add migration upgrade/downgrade and rollback-floor tests.
- [x] 3.4 Run strict OpenSpec validation, formatter, lint, focused unit/integration tests, and the full relevant suite.
- [ ] 3.5 Verify production canary logs show manifest-backed recovery and no new authorization-required loop for the Fourcam trace class.
