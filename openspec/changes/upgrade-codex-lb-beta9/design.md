# Design

## Source and merge

Use the peeled upstream release commit `69f128afcbc616d9f8e924ca6583f7031d75cf82`
as the only upstream release baseline. Merge it into the current CHEK branch,
resolve conflicts by retaining the CHEK contract where upstream does not provide
an equivalent, and document each behavior-sensitive conflict in the PR.

## Recovery contract

The retry circuit must distinguish a capacity failure before acceptance, an
accepted response with no output, and a turn that has emitted output or invoked
a tool. Only the first two may retry, with the latter bound to the same hard
owner. Sustained overload updates account backoff and isolation state while
preserving warm soft-sticky owners for safe continuation. No retry may replay a
request after output or a tool call.

## Deployment safety

Before changing GitOps, record the current source SHA, image digest, pod state,
capacity-error aggregates, migration head, and schema size. Run the beta.9
migration check against a production-sized copy or read-only inspection as
available, take the approved database backup when a forward migration requires
it, and retain the current image as the rollback target. Publish a source-bound,
platform-complete immutable image, update only codex-lb's manifest, and verify
Argo, pod identity, digest, auth, SSE, WebSocket, continuity, and post-rollout
capacity/recovery telemetry.
