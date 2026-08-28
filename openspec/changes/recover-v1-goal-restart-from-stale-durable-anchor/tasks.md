## 1. Contract

- [x] 1.1 Record the proof-gated `/v1` Goal restart and terminal-delivery
      behavior in OpenSpec.
- [x] 1.2 Define negative cases for generic clients, foreign turn states,
      missing thread identity, and account-scoped payloads.

## 2. Regression coverage

- [x] 2.1 Add a production-shape `/v1/responses` integration test that proves
      a verified native Goal restart rotates turn state, dispatches unanchored
      once, completes, and leaves the old durable session/anchor untouched.
- [x] 2.2 Prove a follow-up using the returned turn state continues on the new
      bridge.
- [x] 2.3 Add negative product-path cases for missing marker, generic client,
      missing thread identity, foreign turn state, client previous response,
      conversation, and file/image/account-scoped input.
- [x] 2.4 Add an explicit stale-rejection test that proves one upstream
      dispatch, no same-anchor local rebind, one terminal failure, and an
      unchanged durable anchor.

## 3. Implementation

- [x] 3.1 Add the narrow `/v1` verified Goal-restart predicate and rotate only
      its effective HTTP turn state.
- [x] 3.2 Deliver the already-rewritten stale-anchor terminal event instead of
      raising it into the local same-anchor recovery branch.

## 4. Validation

- [x] 4.1 Run strict OpenSpec validation.
- [x] 4.2 Run focused integration and unit bridge tests.
- [ ] 4.3 Run Ruff format/check, `ty` on changed code, proxy architecture
      checks, and PostgreSQL durable-session integration coverage.
- [x] 4.4 Verify the final diff preserves old durable state and introduces no
      schema or setting change.

> PostgreSQL durable-session coverage for 4.3 is still blocked locally: the
> project allowlisted test was run with a real `postgresql+asyncpg` URL, but
> `127.0.0.1:5432` refused the connection and no local PostgreSQL/Docker
> runtime is available. SQLite results are not counted as PostgreSQL evidence.

## 5. Delivery

- [ ] 5.1 Commit with sidechat and upstream-recovery traceability, push, and
      open a focused PR.
- [ ] 5.2 Satisfy current-head CI, review, and mergeability gates; merge to
      `origin/main`.
- [ ] 5.3 Build/promote an immutable image through GitOps and verify production
      source SHA, digest, health, and migration head.
- [ ] 5.4 Run one no-tool verified Goal probe only after production readback
      proves the new predicate and old-anchor bypass.
