## MODIFIED Requirements

### Requirement: Database pool controls cover isolated request and background sessions

The service SHALL size both the main request pool and the background-task pool
from `database_pool_size` and `database_max_overflow`.  The background pool
SHALL always derive from those two settings; it exists to isolate detached and
scheduler checkouts from the request pool, not to be sized independently.

Foreground middleware, authentication cache misses, identity reads, and proxy
request repositories MUST use the main request pool.  Detached refresh,
scheduler, claim, and background persistence operations MUST use the background
pool.  A foreground checkout timeout MUST fail closed and return a sanitized
HTTP 503 response with `Retry-After`; it MUST NOT expose SQLAlchemy pool details
or bypass firewall/authentication.

#### Scenario: Background pool inherits main pool capacity

- **WHEN** the application creates the background-task DB engine for a pooled backend
- **THEN** the background pool uses `database_pool_size` and `database_max_overflow`
- **AND** no separate background pool sizing setting exists

#### Scenario: Background saturation does not consume request-pool checkouts

- **GIVEN** detached refresh or scheduler work has consumed every available background checkout
- **WHEN** a foreground firewall or API-key cache miss needs database state
- **THEN** it acquires a main/request-pool session
- **AND** it does not wait for a background checkout
- **AND** firewall and authentication remain fail closed

#### Scenario: Foreground checkout timeout is retryable and sanitized

- **WHEN** a foreground database checkout exceeds its bounded pool wait
- **THEN** the HTTP response status is 503
- **AND** the response includes `Retry-After`
- **AND** the response uses the route's native error envelope
- **AND** no SQLAlchemy exception text, connection string, or pool topology is returned

### Requirement: Detached background tasks own short database session lifetimes

Detached background tasks MUST own database session lifetime independently from
cancellable callers.  A task intentionally decoupled from its caller (including
a singleflight refresh held alive by `asyncio.shield`) MUST NOT use a session
owned by the cancellable caller.

Token and account refresh owners MUST perform candidate reads, refresh-claim
operations, fresh state reads, route resolution, CAS persistence, peer adoption,
and claim release through short, independently owned database operations.  All
such database scopes MUST close before admission waits, peer-claim waits, or
external network I/O.  Shielding and cancellation MUST NOT strand a checkout.

#### Scenario: Guardian refresh closes candidate read before upstream work

- **GIVEN** Auth Guardian selects a stale eligible account
- **WHEN** it starts the shielded account refresh
- **THEN** the repository used to read and validate the candidate has already closed
- **AND** refresh database operations use independent short background sessions
- **AND** the upstream OAuth wait holds no candidate-read checkout

#### Scenario: Client cancellation does not strand refresh connections

- **GIVEN** a proxy request joins a shielded singleflight token refresh
- **WHEN** the client task is cancelled while upstream refresh work continues
- **THEN** the refresh finishes or fails through independently owned short sessions
- **AND** refresh claims and guarded token state reach their normal terminal behavior
- **AND** every database checkout returns after the refresh task drains

#### Scenario: Peer rotation remains authoritative

- **GIVEN** another replica rotates the account token while this replica performs upstream OAuth
- **WHEN** this replica attempts its guarded persistence phase
- **THEN** ciphertext-guarded CAS prevents overwriting the peer token
- **AND** the peer row is adopted through a fresh short read
- **AND** the refresh claim is released through an independent short operation
