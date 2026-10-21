## ADDED Requirements

### Requirement: AnyIO synchronization primitives survive cancelled-waiter releases

The project MUST use AnyIO 4.14.0 or later. Releasing an AnyIO lock or semaphore while a cancelled waiter remains queued MUST wake a live waiter or let the next acquirer proceed; it MUST NOT strand an unowned lock or available permit. A deterministic release/cancel/acquire regression MUST cover both AnyIO primitives and the stdlib lock reference.

#### Scenario: AnyIO Lock newcomer acquires after a release coinciding with a cancelled waiter

- **GIVEN** task O holds an `anyio.Lock` and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls `acquire()` before W1 has run its cancellation handler
- **THEN** A acquires the lock within the same bounded wait
- **AND** the lock reports no owner and no waiting tasks after A releases

#### Scenario: AnyIO Semaphore newcomer acquires a permit after a release coinciding with a cancelled waiter

- **GIVEN** task O holds the only permit of an `anyio.Semaphore(1)` and task W1 is queued behind it
- **WHEN** W1 is cancelled, O releases, and a newcomer A calls `acquire()` before W1 has run its cancellation handler
- **THEN** A acquires the permit within the same bounded wait
- **AND** the semaphore reports its full permit count and no waiting tasks after A releases

#### Scenario: Stdlib reference behaviour is preserved

- **GIVEN** the same interleaving is driven against the stdlib `asyncio.Lock`
- **THEN** the newcomer acquires within the same bounded wait

#### Scenario: Dependency floor pins the fixed primitive

- **WHEN** the project dependencies are resolved
- **THEN** the resolved anyio version is at least 4.14.0
- **AND** the regression covering the cancelled-waiter release interleaving passes for both AnyIO primitives and the stdlib reference
