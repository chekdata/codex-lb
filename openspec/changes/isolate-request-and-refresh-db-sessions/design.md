## Context

The service has two equally sized SQLAlchemy engines: one for foreground
requests and one for detached/background work.  Production configured each
engine with two steady connections plus one overflow connection.  Auth Guardian
also ran three refresh candidates concurrently.  Each candidate retained an
outer repository session while a shielded AuthManager task attempted additional
background checkouts and upstream OAuth.  At the same time, request firewall
and authentication cache misses incorrectly requested background sessions.

## Goals / Non-Goals

**Goals:**

- Keep foreground request admission available when background work is busy.
- Ensure no token-refresh database checkout spans admission waits, peer-claim
  waits, or external OAuth I/O.
- Preserve singleflight, refresh claims, ciphertext-guarded CAS, peer adoption,
  shielded cancellation, and fail-closed firewall/auth behavior.
- Expose residual foreground checkout exhaustion as a sanitized retryable 503.

**Non-Goals:**

- Increasing PostgreSQL `max_connections` or adding another pool.
- Weakening refresh claims, response replay proofs, or credential identity.
- Treating a larger pool as the product fix.

## Decisions

- Add one reusable `get_request_session()` context over the existing main
  `SessionLocal`; the FastAPI dependency delegates to the same owner.
- Keep `get_background_session()` for schedulers and detached operations.
- Use the existing `BackgroundAccountsRepository` as AuthManager's production
  port.  Every method opens, uses, detaches from, and closes a short session.
- Guardian closes its candidate repository before constructing or awaiting the
  shielded refresh owner.
- ProxyService accepts an explicit per-operation refresh repository in
  production while retaining the existing factory seam for isolated tests.
- SQLAlchemy checkout timeout is a fail-closed 503 with OpenAI/dashboard-native
  envelopes and `Retry-After`; exception details and pool topology are never
  returned to clients.

## Risks / Trade-offs

- [A detached ORM object is accessed after close] → The per-operation
  repository expunges returned objects before closing; focused tests exercise
  Guardian use after candidate scope exit.
- [A caller bypasses production injection] → The application composition
  root explicitly injects `BackgroundAccountsRepository`; the compatibility
  factory remains only for tests/custom constructors.
- [The main pool is itself exhausted] → Fail closed with a stable 503 and
  `Retry-After`, preserving firewall enforcement and hiding SQLAlchemy details.
- [A pool-size increase masks the bug] → Session-lifetime tests assert the
  repository scope is closed at the upstream barrier rather than relying on
  capacity alone.
