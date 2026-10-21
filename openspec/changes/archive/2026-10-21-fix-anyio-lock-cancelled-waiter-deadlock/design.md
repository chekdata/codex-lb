# CHEK backport design

This records the implemented CHEK adaptation of the imported upstream change.

## Context

See proposal.md for the cancelled-waiter interleaving. CHEK's existing bridge uses AnyIO synchronization primitives and must preserve ownership of pending requests and sockets.

## Decisions

Require AnyIO >=4.14 and resolve 4.15.1 in uv.lock. Use the upstream primitive fix instead of replacing locks or adding application-side ownership workarounds. Keep the deterministic release/cancel/acquire test for both AnyIO primitives and the asyncio reference; it failed for AnyIO 4.13.0 and passed after dependency synchronization.

## Risks and rollout

Dependency upgrades can affect cancellation paths. The three affected test modules passed 713 cases, and source PR #52 and main CI passed PostgreSQL, browser and image gates. No schema or Helm changes are part of this backport. Production uses immutable SHA/digest promotion; see the production verification record in openspec/specs/responses-api-compat/context.md.
