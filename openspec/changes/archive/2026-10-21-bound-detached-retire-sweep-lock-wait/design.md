# CHEK backport design

This records the implemented CHEK adaptation of the imported upstream change.

## Context

See proposal.md for the request-path lock wait. Lifecycle owners remain responsible for final resource cleanup.

## Decisions

Use asyncio.timeout in the calling task so AnyIO lock acquisition and release retain the same owner. Keep the optional timeout unset for lifecycle callers. Retain the existing request, reservation and socket ownership checks.

The later upstream bound-detached-retire-sweep-deadline change strengthens the per-lock limit to one aggregate five-second monotonic deadline. CHEK applies both changes together using time.monotonic. Main responses-api-compat/spec.md already contains this stronger requirement and its cancellation/finalization scenarios; do not overwrite it with this historical delta when archiving.

## Risks and rollout

A timeout defers cleanup while leaving the session tracked; later sweeps and lifecycle owners can retire it. Tests cover held and free locks, preserved ownership, shrinking aggregate budget and cancellation. The 713 affected tests and full source/main CI passed. No schema or Helm change is required. See openspec/specs/responses-api-compat/context.md for immutable production identities and verification.
