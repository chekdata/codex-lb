# Change: publish-chek-internal-images

## Why

CHEK deploys its maintained `chekdata/codex-lb` fork through ArgoCD. Production
must consume an image built from the reviewed fork commit rather than an
upstream image or a mutable local build, but the fork currently has no
repository-owned image publisher.

## What Changes

- Add a focused GitHub Actions workflow that publishes the fork to both
  `ghcr.io/chekdata/codex-lb` and CHEK's Beijing production registry after a
  push to `main` or an explicit manual run.
- Publish both supported Linux architectures in one manifest.
- Publish an immutable full-SHA tag for GitOps while granting the workflow only
  read-content and write-package access; do not publish a mutable `main` tag.
- Fail closed when the Beijing full-SHA tag already exists or either registry
  state is uncertain, while allowing an interrupted publication to restore a
  missing Beijing mirror from an existing immutable GHCR image.
- Serialize all runs for the same commit across branch and tag aliases so the
  absence check and first publication cannot race.

## Impact

- Affected capability: `github-automation`
- Affected automation: `.github/workflows/internal-image.yml`
- Production manifests can pin a reviewed `sha-*` image from the in-region
  registry without depending on cross-region pulls, upstream publishing
  permissions, or abbreviated-tag collisions.
