# Tasks

## Specification

- [x] Add requirements for mixed reader/terminal eventless ordering, atomic
      circuit-open anchor settlement, and fail-closed clear failures.
- [x] Add scenarios for concurrent duplicate settlement and cooldown expiry.

## Implementation

- [x] Centralize poison settlement at the retry-circuit threshold.
- [x] Remove the reader-only seven-strike clear gate.
- [x] Fence anchor injection and quarantine expiry until durable clear succeeds.

## Regression coverage

- [x] Cover reader→terminal and terminal→reader `previous_response_not_found`
      sequences.
- [x] Cover clear failure/fencing, duplicate callbacks, completed-response
      reset, and clean-close exclusion.
- [x] Cover cooldown expiry with no stale anchor reinjection.

## Verification

- [x] Run bridge unit/integration tests, lint, type checks, and strict OpenSpec
      validation.
