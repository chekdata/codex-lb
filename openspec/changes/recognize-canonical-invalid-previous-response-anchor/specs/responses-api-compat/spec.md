## MODIFIED Requirements

### Requirement: Single HTTP bridge previous-response misses recover or fail closed
When an HTTP bridge session receives an anonymous upstream `previous_response_not_found` error for a single pending follow-up request, the service MUST treat the error as an internal continuity-loss signal. The same treatment MUST apply when upstream returns `code=invalid_request_error` with the exact canonical message `Invalid previous_response_id.` (allowing only quote/backtick and terminal-period variations) and either omits `param` or sets `param=previous_response_id`. It MUST either recover through the existing previous-response rebind path or rewrite the error to a retryable continuity failure instead of forwarding the raw upstream invalid-request error. Other `invalid_request_error` messages or conflicting `param` values MUST retain their ordinary request-error classification.

#### Scenario: single pending HTTP bridge follow-up loses previous-response continuity
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` bridge session has exactly one pending request with `previous_response_id`
- **AND** upstream emits `previous_response_not_found` without a `response.id`
- **THEN** the service attempts the existing previous-response recovery path
- **AND** if recovery is unavailable, it emits a retryable continuity failure for that request
- **AND** the downstream error code is not `previous_response_not_found`

#### Scenario: canonical invalid previous-response anchor omits param
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` bridge session has a pending request with `previous_response_id`
- **AND** upstream emits `code=invalid_request_error`, no `param`, and the exact message `Invalid previous_response_id.` before `response.created`
- **THEN** the service classifies the event as previous-response continuity loss
- **AND** it uses the same proof-gated recovery or fail-closed path as `previous_response_not_found`
- **AND** it does not forward the raw invalid-request response

#### Scenario: unrelated invalid request remains a request error
- **WHEN** upstream emits `code=invalid_request_error`
- **AND** its `param` conflicts with `previous_response_id` or its message contains additional request-validation text
- **THEN** the service MUST NOT classify that error as previous-response continuity loss

### Requirement: Public Responses errors mask previous-response misses
Public Responses endpoints MUST NOT return an OpenAI-shaped previous-response continuity error to clients. If a lower layer still raises or collects that error, the API layer MUST rewrite it to a retryable `stream_incomplete` continuity failure and remove the missing response id from the public payload. The exact canonical `Invalid previous_response_id.` invalid-request shape MUST use the same masking and recovery path only when its `param` is absent or matches `previous_response_id`; unrelated invalid requests MUST remain unchanged.

#### Scenario: API layer receives an upstream previous-response miss
- **WHEN** a public `/responses`, `/v1/responses`, `/responses/compact`, or `/v1/responses/compact` handler receives an error with `code=previous_response_not_found`
- **OR** it receives `code=invalid_request_error` with `param=previous_response_id` and a message saying the previous response was not found
- **THEN** the response status is retryable
- **AND** the public error code is `stream_incomplete`
- **AND** the missing `previous_response_id` is not exposed in the response body

#### Scenario: canonical invalid anchor is not exposed
- **WHEN** a public Responses handler receives `code=invalid_request_error` with an absent or matching `param` and the exact canonical message `Invalid previous_response_id.`
- **THEN** the raw invalid-request response is not exposed
- **AND** the request uses the existing proof-gated recovery or retryable continuity failure path
