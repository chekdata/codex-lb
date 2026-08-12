# Change: publish-chek-internal-images

## Why

CHEK deploys its maintained `chekdata/codex-lb` fork through ArgoCD. Production
must consume an image built from the reviewed fork commit rather than an
upstream image or a mutable local build, but the fork currently has no
repository-owned image publisher.

## What Changes

- Add a focused GitHub Actions workflow that publishes the fork to
  `ghcr.io/chekdata/codex-lb` after a push to `main` or an explicit manual run.
- Publish both supported Linux architectures in one manifest.
- Publish an immutable short-SHA tag for GitOps and a mutable `main` convenience
  tag while granting the workflow only read-content and write-package access.

## Impact

- Affected capability: `github-automation`
- Affected automation: `.github/workflows/internal-image.yml`
- Production manifests can pin a reviewed `sha-*` image without depending on
  upstream publishing permissions or tags.
