## ADDED Requirements

### Requirement: CHEK fork publishes internally owned container images

The `chekdata/codex-lb` repository SHALL build and publish a container image to
`ghcr.io/chekdata/codex-lb` after changes land on `main` and when an operator
explicitly dispatches the workflow. The published manifest MUST support
`linux/amd64` and `linux/arm64`, MUST include an immutable `sha-<short-commit>`
tag, and MAY update the `main` tag for operator convenience. Production GitOps
consumers MUST be able to select the immutable tag.

The workflow MUST use pinned action revisions and MUST limit its repository
permissions to reading contents and writing packages. Pull request events MUST
NOT publish images.

#### Scenario: Main commit produces an immutable multi-architecture image

- **WHEN** a reviewed commit lands on `main`
- **THEN** the workflow publishes `ghcr.io/chekdata/codex-lb:sha-<short-commit>`
- **AND** the image manifest supports `linux/amd64` and `linux/arm64`
- **AND** the same run updates `ghcr.io/chekdata/codex-lb:main`

#### Scenario: Pull request validation cannot publish a package

- **WHEN** a pull request is opened or updated
- **THEN** the internal image workflow does not run from that event
- **AND** no image tag is published by the pull request

#### Scenario: Operator can rebuild the current selected revision

- **WHEN** an operator manually dispatches the workflow on an allowed ref
- **THEN** the workflow builds and publishes the selected commit with its
  immutable short-SHA tag
