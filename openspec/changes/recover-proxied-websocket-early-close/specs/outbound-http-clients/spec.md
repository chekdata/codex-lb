## ADDED Requirements

### Requirement: Proxied WebSocket setup closes fail as pre-dispatch transport errors

When an upstream WebSocket uses an HTTP proxy and the transport closes while
TLS setup is transferring the transport to the WebSocket protocol, before the
protocol's `connection_made()` initializes receive state, the service MUST
complete connection-lost bookkeeping without dereferencing uninitialized
receive or transport attributes. The connection attempt MUST fail as a typed
pre-dispatch transport error and MUST NOT leave an HTTP Responses stream
pending without a terminal event. Once `connection_made()` has run, the
dependency's established-connection close semantics MUST remain unchanged.

#### Scenario: proxy transport closes before connection setup completes

- **GIVEN** a secure upstream Responses WebSocket is routed through an HTTP proxy
- **AND** the proxy transport closes before `connection_made()` initializes the receive assembler
- **WHEN** the WebSocket dependency reports `connection_lost()`
- **THEN** the service completes the connection-lost waiter without raising an event-loop callback exception
- **AND** classifies the attempt as a pre-dispatch transport failure
- **AND** no request is treated as having reached upstream

#### Scenario: established proxied connection keeps normal close semantics

- **GIVEN** the proxied WebSocket completed `connection_made()` and initialized receive state
- **WHEN** the established connection closes
- **THEN** the dependency's normal close path handles pending receives, pings, and drain waiters
- **AND** the adapter does not weaken or suppress the established-connection failure classification
