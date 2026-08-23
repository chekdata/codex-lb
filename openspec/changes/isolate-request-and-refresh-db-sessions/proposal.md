## Why

Production `/v1/responses` requests returned raw HTTP 500 errors even though
PostgreSQL had ample server capacity.  Three Auth Guardian refresh candidates
could retain all three background-pool checkouts while waiting for nested
refresh work and upstream OAuth.  Firewall and API-key cache misses also used
that background pool, so scheduler starvation reached the request front door.

## What Changes

- Give foreground middleware, authentication, and proxy repositories an
  explicit main/request-pool session boundary.
- Make Guardian candidate reads and token-refresh persistence use short,
  per-operation background sessions that close before waits or upstream I/O.
- Return a sanitized, retryable HTTP 503 response when a foreground database
  checkout times out instead of exposing an internal QueuePool error as 500.
- Add behavioral coverage for cancellation cleanup, pool ownership, Guardian
  refresh lifetime, and the externally visible retry contract.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backends`: Separate foreground and detached/background checkout
  ownership and forbid token refresh from holding a checkout across network I/O.

## Impact

Affected surfaces are database session ownership, firewall/API-key/Codex
identity cache misses, Auth Guardian refresh, ProxyService token refresh, the
pool-timeout error envelope, and focused unit/integration tests.  No schema,
migration, credential, API-key identity, or replay-safety rule changes.
