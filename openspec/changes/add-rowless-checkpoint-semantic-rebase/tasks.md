# Tasks

- [x] Add non-cascading authority schema, migration, repository, and lifecycle tests.
- [x] Add content-free input/contract/direct-call ledger fingerprints and fail-closed matching tests.
- [x] Capture zero-event explicit stale-anchor rejects and locally stop repeated same-contract sends.
- [x] Add trusted-proxy dashboard list/challenge/approve API and content-free operator response contract.
- [x] Bind approval to the exact client-owned checkpoint receipt and account-neutral dispatch intent.
- [x] Claim one generation before selection/connect, bind UNKNOWN journal before send, and restore only
      physically proven unsent attempts.
- [x] Atomically publish the new durable checkpoint and consume the authority on completion.
- [x] Cover three sanitized production structures, concurrent claims, restart/cleanup, all drift cases,
      PR #19 ambiguous predecessor, and irreversible-effect count.
- [x] Run strict OpenSpec validation, focused unit/integration tests, architecture, Ruff/format, and ty.
