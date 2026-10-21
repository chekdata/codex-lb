# Upgrade codex-lb to upstream v1.25.0-beta.9 with CHEK customizations

## Why

The deployed CHEK build is based on `f301c4574e905abcba1bc17cee76e16359858580`,
which predates the upstream overload recovery series and the beta.9 warm-session
isolation correction. Capacity responses can therefore interrupt a request even
when a safe retry or an already-established session is available.

## What Changes

- Merge the exact upstream release tag `v1.25.0-beta.9`, peeled commit
  `69f128afcbc616d9f8e924ca6583f7031d75cf82`, into the CHEK branch instead of
  transplanting only selected upstream commits.
- Preserve CHEK authentication, model routing, session continuity, usage
  settlement, cleanup, schema overrides, and the PR #52 reliability fixes while
  resolving conflicts against the beta.9 implementations.
- Verify capacity recovery on HTTP Responses and direct WebSocket paths for
  pre-accept retry, accepted/no-output retry, account backoff/isolation, hard
  session ownership, and no replay after output or tool calls.
- Reconcile beta.9 defaults with the production Helm values without silently
  changing the requested model or disabling CHEK safeguards.
- Validate migration ancestry, production schema readiness, rollback to the
  current immutable image, and publish a traceable internal image through the
  existing GitOps flow.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: capacity failures may be retried only while the
  request remains replay-safe, with accepted output-free continuations bound to
  their owner and established output/tool turns never replayed.
- `proxy-architecture`: account overload backoff and isolation must preserve
  warm reusable sessions while excluding sustained-overload accounts from fresh
  selection.
- `database-migrations`: the beta.9 migration graph must be checked against the
  CHEK production schema before rollout and remain rollback-compatible.
- `deployment-installation`: the production image and Helm values identify the
  exact beta.9 source and immutable digest.

## Impact

- Upstream application, dependency, migration, Helm, and regression-test files
  may change as part of the exact beta.9 merge.
- Production requires an immutable image and a GitOps manifest update; no other
  service is in scope.
- Existing CHEK auth, billing, continuity, cleanup, and database-schema
  contracts remain required acceptance criteria.
