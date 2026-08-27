## Why

The stable v1.24.0 synchronization removed the proxied WebSocket setup guard
that had already been merged in the CHEK branch. When a shared HTTP proxy
closes the tunnel during the TLS handoff, `websockets` can call
`connection_lost()` before receive state exists. The resulting callback failure
leaves Responses turns without a terminal event and causes avoidable retries
and stale continuation anchors.

## What Changes

- Restore a narrowly scoped `ClientConnection` adapter for pre-`connection_made`
  proxy closes.
- Retry exactly one fresh shared-proxy tunnel on the selected account before
  returning an account-neutral, typed setup failure.
- Preserve normal established-connection close handling and existing routed
  failover semantics.
- Add unit and public HTTP Responses bridge regressions for early close,
  transient proxy handshake EOF, and retry exhaustion.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: restore the existing proxied WebSocket pre-dispatch
  setup contract on the current stable base.

## Impact

The change is limited to the upstream WebSocket client, its focused unit and
HTTP bridge regressions, and OpenSpec tracking. It adds no setting, dependency,
migration, or public API.
