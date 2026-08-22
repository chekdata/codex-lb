## Why

The HTTP Responses bridge currently opens its retry circuit after a clean
upstream WebSocket close even when the replacement socket also closes before
producing any response event. A downstream idle-recovery task can also replace
the upstream socket without restarting its reader. Closing the old socket then
wakes that stale reader, which misclassifies the proxy-initiated close as an
upstream failure and retires work already moved to the replacement socket.
Together these behaviors make a transient handoff issue visible as a reconnect
loop and require the Codex client to be restarted.

## What Changes

- Permit one additional pre-visible replay when the replacement upstream
  WebSocket closes cleanly before any response event.
- Add bounded, configurable jitter before that additional replay to avoid
  synchronized reconnects.
- Emit a dedicated diagnostic event for the additional clean-close replay.
- Keep the allowance hard-capped at one and preserve all existing no-replay
  behavior after downstream-visible output or continuity-sensitive state.
- When recovery is initiated outside the upstream reader, cancel and await the
  old reader before closing its socket, then start exactly one reader for the
  replacement socket.
- Keep the shared session live while the replacement socket opens so concurrent
  idle pruning cannot evict and fail its pending response during the handoff.
- Start silent pre-response recovery with enough headroom to reconnect before
  the downstream client's request timeout boundary.
- Do not let a proxy-initiated close of a superseded socket retire pending work
  on the replacement socket or increment the retry circuit.
- Detect a stuck pre-response gate from the absence of upstream activity and
  response creation, rather than admission flags alone. Give requests with a
  prior continuity anchor a bounded two-threshold grace period, and emit
  diagnostic state when the watchdog skips a candidate.
- Detect a socket that is already closed before the transport adapter invokes
  its send primitive, reconnect once, and dispatch the request exactly once on
  the replacement socket. Preserve fail-closed handling for every exception
  raised after the send primitive is invoked because delivery is then
  ambiguous.
- Return one atomic retry-circuit decision containing the failure class and
  remaining cooldown. Use it for both the HTTP `Retry-After` header and an
  accurate operator-facing message instead of describing every WebSocket
  failure as a timeout.
- Treat an upstream rejection of a proxy-injected `previous_response_id` as a
  stale continuity anchor, not as a reason to inject the same identifier into
  another physical WebSocket. Replay immediately only when the immutable
  durable proof covers the complete client context; otherwise quarantine the
  logical key and recover on the next complete client resend while keeping
  delta-only requests fail-closed.

## Impact

- Repeated clean handoffs can recover transparently without an immediate
  terminal circuit-open response.
- The retry remains bounded and does not create an unbounded replay loop.
- Reader ownership follows the active socket across idle recovery, preventing
  locally generated close frames from being counted as upstream instability.
- Adds the `http_bridge_retry_circuits` durable table and migration so retry
  cooldown state survives cross-replica clean-close and incomplete-stream
  failures.
- Adds a forward-only request-usage rollup repair migration for deployments
  already stamped at the previous merge head, so changing migration ancestry
  cannot leave startup schema-drift checks failing.
- Clients receive a consistent integer cooldown hint and failure-class copy;
  clients remain responsible for honoring `Retry-After` rather than exhausting
  their local retry budget inside the advertised cooldown.
- A stale durable response anchor can no longer strand a long-lived desktop
  task in an `Invalid previous_response_id` / reconnect / cooldown loop.
