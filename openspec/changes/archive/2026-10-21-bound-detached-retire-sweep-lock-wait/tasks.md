> Imported upstream completion record. CHEK backport validation is recorded in `openspec/specs/responses-api-compat/context.md`; upstream checkmarks do not assert a local CI result.

## 1. Bound the Sweep

- [x] 1.1 Add `lock_wait_timeout_seconds` to `_retire_http_bridge_after_drain_if_ready` (bounded acquire, warning on timeout, unchanged locked body, unbounded when `None`).
- [x] 1.2 Pass a fixed 5 second bound from the per-request detached-session sweep.

## 2. Regression Coverage

- [x] 2.1 Sweep against a permanently held detached lock returns within the bound, closes nothing, and leaves the lock owned by its holder.
- [x] 2.2 Sweep against a free lock still retires; a lifecycle owner without the bound still waits.

## 3. Validation

- [x] 3.1 Focused unit tests, lint, format, type, and architecture checks.
- [x] 3.2 Strict change-local OpenSpec validation.

## CHEK completion

- [x] Reproduce and verify the backport against CHEK ownership and cancellation behavior; 713 affected tests passed.
- [x] Verify PR #52 and main CI, then production through ops-bootstrap #3074: exact source/image identity, Ready pod with zero restarts, AnyIO 4.15.1, database/ring readiness and public health.
- [x] Verify main specs already incorporate this change and the later aggregate-deadline strengthening.
