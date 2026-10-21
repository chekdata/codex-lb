# Tasks

- [ ] Reconfirm current CHEK repository, upstream tag, production image, and
      GitOps state.
- [ ] Compare beta.9 ancestry and dependencies with CHEK custom code and PR #52;
      resolve and test the full merge.
- [ ] Audit HTTP bridge and WebSocket capacity recovery defaults and deployment
      overrides; add focused regressions for replay safety, ownership, backoff,
      and isolation.
- [ ] Inspect Alembic heads and production schema size; document backup and
      rollback conditions.
- [ ] Run project checks, CI, security scan, independent review, and release
      guard on the exact candidate SHA.
- [ ] Build and publish the traceable immutable internal image and update only
      codex-lb's GitOps manifest.
- [ ] Verify production version, source, digest, auth, streaming, continuity,
      health, and before/after capacity telemetry; record measurement limits.
