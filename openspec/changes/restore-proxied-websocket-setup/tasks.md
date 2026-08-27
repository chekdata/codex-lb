## 1. Regression

- [x] 1.1 Add coverage for a proxy close before `connection_made()` and confirm
  the event loop receives no callback exception.
- [x] 1.2 Add coverage for one fresh same-account tunnel retry and typed
  exhaustion without account backoff or cross-account failover.
- [x] 1.3 Add a public HTTP Responses bridge regression for the shared-proxy
  exhaustion contract.

## 2. Implementation

- [x] 2.1 Restore the pre-setup-safe `ClientConnection` adapter without changing
  established close semantics.
- [x] 2.2 Retry only transient shared-proxy setup failures and retain sanitized
  failure provenance after exhaustion.

## 3. Verification

- [x] 3.1 Run focused WebSocket unit and HTTP bridge integration tests.
- [x] 3.2 Run strict OpenSpec validation with the compatible
  `@fission-ai/openspec` CLI (57/57 specs passed).
- [x] 3.3 Run Ruff, type checks, and inspect the final diff for unrelated
  changes.
