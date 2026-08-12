## ADDED Requirements

### Requirement: CHEK fork publishes internally owned container images

The `chekdata/codex-lb` repository SHALL build and publish a container image to
`ghcr.io/chekdata/codex-lb` after changes land on `main` and when an operator
explicitly dispatches the workflow. The published manifest MUST support
`linux/amd64` and `linux/arm64`, MUST include an immutable `sha-<full-commit>`
tag, and MAY update the `main` tag for operator convenience. Production GitOps
consumers MUST be able to select the immutable tag.

Before publishing, the workflow MUST prove that the full-SHA tag does not
already exist. It MUST fail without building when the tag exists and MUST fail
closed when a registry or network error prevents that absence check. It MUST
NOT overwrite a previously published full-SHA tag.

The workflow MUST use pinned action revisions and MUST limit its repository
permissions to reading contents and writing packages. Pull request events MUST
NOT publish images.

#### Scenario: Main commit produces an immutable multi-architecture image

- **WHEN** a reviewed commit lands on `main`
- **THEN** the workflow publishes `ghcr.io/chekdata/codex-lb:sha-<full-commit>`
- **AND** the image manifest supports `linux/amd64` and `linux/arm64`
- **AND** the same run updates `ghcr.io/chekdata/codex-lb:main`

#### Scenario: Pull request validation cannot publish a package

- **WHEN** a pull request is opened or updated
- **THEN** the internal image workflow does not run from that event
- **AND** no image tag is published by the pull request

#### Scenario: Operator can rebuild the current selected revision

- **WHEN** an operator manually dispatches the workflow on an allowed ref
- **AND** the selected commit's full-SHA tag does not yet exist
- **THEN** the workflow builds and publishes the selected commit with its
  immutable full-SHA tag

#### Scenario: Repeated publication cannot change an immutable tag

- **GIVEN** the selected commit's full-SHA tag already exists in GHCR
- **WHEN** the workflow is rerun or manually dispatched for that commit
- **THEN** it fails before the image build
- **AND** the existing tag is not overwritten

#### Scenario: Registry uncertainty fails closed

- **GIVEN** the workflow cannot determine whether the full-SHA tag exists
- **WHEN** the pre-publish check encounters a registry or network error
- **THEN** the workflow fails without publishing
