## Why

The production Responses bridge observed an HTTP-proxy WebSocket connection
closing while TLS setup was still transferring the transport to
`websockets.ClientConnection`. The dependency invoked `connection_lost()`
before `connection_made()` initialized its receive assembler, raised an
uncaught `AttributeError`, and left the affected streamed request without a
terminal `response.completed` event. A later retry succeeded, but the first
request was already broken.

## What Changes

- Use a narrowly scoped `ClientConnection` adapter for proxied upstream
  WebSockets.
- Treat a close before `connection_made()` as a pre-dispatch transport failure
  while completing the dependency's connection-lost waiter without touching
  uninitialized receive or transport state.
- Keep established connections on the dependency's normal close path.
- Preserve the existing typed, retryable pre-dispatch failure contract so an
  HTTP bridge may select another eligible account before any
  `response.create` can be dispatched.
- Add adapter-level and public HTTP Responses regressions for the exact
  pre-`connection_made()` close shape.
