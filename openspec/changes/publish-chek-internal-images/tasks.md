# Tasks: publish-chek-internal-images

## 1. Internal image publisher

- [x] 1.1 Add a main-branch and manual-dispatch GHCR workflow with pinned actions
- [x] 1.2 Build and publish a multi-architecture image for amd64 and arm64
- [x] 1.3 Emit only an immutable full-SHA tag with minimal permissions
- [x] 1.4 Refuse to overwrite an existing full-SHA tag and fail closed when its
      registry state cannot be confirmed
- [x] 1.5 Serialize publication by full commit SHA across branch and tag refs

## 2. Verification

- [x] 2.1 Validate the workflow YAML and repository action policy
- [x] 2.2 Run strict OpenSpec validation
- [ ] 2.3 Merge the focused PR and verify the first main-branch image manifest
