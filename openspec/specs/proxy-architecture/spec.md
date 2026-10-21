# proxy-architecture Specification

## Purpose
Structural fitness gates for the proxy: ProxyService stays a stable façade and internal decomposition (selection orchestration, bridge mixins) cannot drift behavior or re-grow god-modules.
## Requirements
### Requirement: Proxy architecture fitness gates are enforced

The repository SHALL enforce the accepted proxy architecture thresholds during
the required lint gate. `app/modules/proxy/service.py` SHALL contain no more
than 2,600 lines, `app/modules/proxy/load_balancer.py` SHALL contain no more
than 3,021 lines, and `LoadBalancer.select_account()` SHALL span no more than
527 lines. Implementations SHALL restore or lower these ratchets rather than
increase, bypass, or remove them to make CI pass.

#### Scenario: Multiple ratchets are violated

- **WHEN** more than one independent proxy architecture threshold or boundary is violated
- **THEN** one architecture-check run reports every independently evaluable violation in deterministic order
- **AND** the check exits non-zero

#### Scenario: All architecture gates pass

- **WHEN** every proxy architecture threshold and boundary is satisfied
- **THEN** the architecture check exits zero
- **AND** it reports that the proxy architecture checks passed

### Requirement: ProxyService remains a stable façade

`app.modules.proxy.service.ProxyService` and the required compatibility exports
SHALL remain available to existing consumers. Behavior extracted from
`ProxyService` or `service.py` SHALL be owned by focused private modules under
`app/modules/proxy/_service/`.
Compatibility shims SHALL remain re-export-only and private service domains
SHALL comply with the repository's explicit cross-domain dependency policy.

#### Scenario: Existing consumers import the proxy façade

- **WHEN** an existing caller imports `ProxyService` or a required compatibility export from `app.modules.proxy.service`
- **THEN** the import resolves to behavior compatible with the pre-change façade
- **AND** no caller migration is required

### Requirement: Account selection orchestration is decomposed without behavior drift

`LoadBalancer.select_account()` SHALL remain the public account-selection entry
point and SHALL delegate cohesive sticky-key retry orchestration and policy to a
private, protocol-typed load-balancer implementation unit. The decomposition
MUST preserve account scope, continuity ownership, security authorization,
exclusions, routing policy, quota and health filtering, concurrency caps,
affinity, stale-state retries, lease cleanup, persistence, result metadata, and
error-code behavior.

#### Scenario: Selection succeeds with or without stickiness

- **WHEN** a request is eligible for account selection with either a sticky key or no sticky key
- **THEN** the selected account, lease, persisted runtime state, and result metadata match the pre-change behavior for the same inputs

#### Scenario: Ownership or capacity prevents selection

- **WHEN** continuity ownership is ambiguous or conflicting, a hard-affinity owner is unavailable, or account caps are exhausted
- **THEN** selection returns the same fail-closed outcome, error code, and mapping-preservation behavior as before the decomposition

#### Scenario: Persistence or cancellation interrupts selection

- **WHEN** persistence fails, a selected row becomes stale, or the selection task is cancelled
- **THEN** acquired leases are released exactly once
- **AND** retries and final errors follow the existing bounded behavior

#### Scenario: Non-sticky selection observes a cache-generation change

- **WHEN** non-sticky selection acquires a lease and the selection-input cache generation changes during persistence
- **THEN** the acquired lease is released exactly once
- **AND** non-sticky selection reloads its inputs and retries within the existing bound

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
