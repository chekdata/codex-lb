## ADDED Requirements

### Requirement: Per-request detached-session retire sweep bounds its lock wait

The per-request detached-session retire sweep MUST bound each pending-lock wait to five seconds. A timeout MUST leave the session tracked, preserve lock ownership, and emit a warning. Lifecycle owners MUST keep waiting without this bound so their retirement decisions remain authoritative.

#### Scenario: Busy detached lock does not park the request path

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is held by another task for longer than the bound
- **WHEN** a request runs the fail-safe sweep
- **THEN** the sweep returns after the bound without closing the session
- **AND** a warning names the skipped session
- **AND** the lock remains owned by its holder with no stranded waiter

#### Scenario: Free detached lock still retires

- **GIVEN** a detached session flagged `retire_after_drain` whose `pending_lock` is free and which no turn owns
- **WHEN** a request runs the fail-safe sweep
- **THEN** the session is retired exactly as before

#### Scenario: Lifecycle owners keep the unbounded wait

- **GIVEN** a drain or close path calls the retire check without a bound while another task briefly holds the lock
- **WHEN** the holder releases
- **THEN** the retire check proceeds and retires the session
