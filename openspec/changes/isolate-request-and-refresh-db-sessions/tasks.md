## 1. Session ownership

- [x] 1.1 Add the explicit main/request-pool session context and preserve robust rollback/close semantics.
- [x] 1.2 Route firewall, API-key, Codex identity, and foreground proxy repositories through the request pool.
- [x] 1.3 Bind production token refresh to per-operation background repositories.

## 2. Refresh lifetime

- [x] 2.1 Close Guardian candidate reads before shielded refresh work.
- [x] 2.2 Preserve singleflight, claim, CAS, peer-adoption, and cancellation semantics.

## 3. Failure contract

- [x] 3.1 Render database checkout exhaustion as sanitized HTTP 503 plus `Retry-After`.
- [x] 3.2 Keep firewall/auth fail closed and prevent internal pool detail leakage.

## 4. Verification

- [x] 4.1 Add request-pool ownership, cancellation, Guardian lifetime, and timeout-envelope tests.
- [x] 4.2 Run complete proportional unit/integration, lint, type, OpenSpec, and diff gates.
- [ ] 4.3 Deploy through normal source/image/GitOps reviews and prove multi-session production stability.
