> Imported upstream completion record. CHEK backport validation is recorded in `openspec/specs/responses-api-compat/context.md`; upstream checkmarks do not assert a local CI result.

## 1. Dependency Floor

- [x] 1.1 Declare `anyio>=4.14.0` in `pyproject.toml` and refresh `uv.lock` (anyio 4.15.1).

## 2. Regression Coverage

- [x] 2.1 Add a deterministic unit test that reproduces the release-with-cancelled-waiter interleaving for `anyio.Lock`, `anyio.Semaphore`, and the stdlib `asyncio.Lock` reference and asserts the newcomer acquires.
- [x] 2.2 Confirm the test fails against anyio 4.13.0 and passes against the locked version.

## 3. Validation

- [x] 3.1 Run the focused unit test, lint, type, and architecture checks.
- [x] 3.2 Run strict change-local OpenSpec validation.

## CHEK completion

- [x] Reproduce and verify the backport against CHEK ownership and cancellation behavior; 713 affected tests passed.
- [x] Verify PR #52 and main CI, then production through ops-bootstrap #3074: exact source/image identity, Ready pod with zero restarts, AnyIO 4.15.1, database/ring readiness and public health.
- [x] Verify main specs already incorporate this change and the later aggregate-deadline strengthening.
