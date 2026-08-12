## ADDED Requirements

### Requirement: CHEK fork publishes internally owned container images

The `chekdata/codex-lb` repository SHALL build and publish a container image to
`ghcr.io/chekdata/codex-lb`, then request an authenticated private operations
workflow to mirror that image into CHEK's Beijing production registry, after
changes land on `main` and when an operator explicitly dispatches the workflow.
Each published manifest MUST support `linux/amd64` and `linux/arm64`, MUST
include an immutable `sha-<full-commit>` tag, and MUST NOT publish a mutable
`main` tag. Production GitOps consumers MUST be able to select the immutable
in-region tag.

Before publishing, the source workflow MUST prove that the GHCR full-SHA tag
does not already exist and MUST fail closed when a registry or network error
prevents that absence check. It MUST NOT overwrite a previously published
full-SHA tag. After the build succeeds, it MUST dispatch only the selected full
commit SHA and resulting image digest to the private operations repository;
production registry write credentials MUST NOT be configured in the public
fork. The private mirror workflow MUST verify that the selected commit belongs
to the `main` history and MUST use repository-scoped, temporary registry
credentials. Runs selecting the same commit through different branch or tag
refs MUST be serialized by the full commit SHA and MUST NOT cancel the run that
currently owns publication.

The workflow MUST use pinned action revisions and MUST limit its repository
permissions to reading contents and writing packages. Pull request events MUST
NOT publish images.

#### Scenario: Main commit produces an immutable multi-architecture image

- **WHEN** a reviewed commit lands on `main`
- **THEN** the workflow publishes `ghcr.io/chekdata/codex-lb:sha-<full-commit>`
- **AND** it requests the private operations workflow to mirror that exact SHA
  and digest to CHEK's Beijing production registry
- **AND** the image manifest supports `linux/amd64` and `linux/arm64`
- **AND** the run does not publish a mutable `main` tag

#### Scenario: Pull request validation cannot publish a package

- **WHEN** a pull request is opened or updated
- **THEN** the internal image workflow does not run from that event
- **AND** no image tag is published by the pull request

#### Scenario: Operator can rebuild the current selected revision

- **WHEN** an operator manually dispatches the workflow on an allowed ref
- **AND** the selected commit's full-SHA tag does not yet exist
- **THEN** the workflow builds and publishes the selected commit with its
  immutable full-SHA tag

#### Scenario: Production credentials stay private

- **WHEN** the public fork publishes a reviewed image
- **THEN** it sends only the full commit SHA and built digest to the private
  operations repository
- **AND** the public fork does not receive production registry write credentials

#### Scenario: Private mirror validates production provenance

- **WHEN** the private operations workflow receives a mirror request
- **THEN** it verifies the commit belongs to the `chekdata/codex-lb` main history
- **AND** it verifies the GHCR tag resolves to the dispatched digest
- **AND** it uses temporary registry credentials scoped to `prod/codex-lb`

#### Scenario: Registry uncertainty fails closed

- **GIVEN** the workflow cannot determine whether either full-SHA tag exists
- **WHEN** the pre-publish check encounters a registry or network error
- **THEN** the workflow fails without publishing

#### Scenario: Branch and tag aliases cannot race publication

- **GIVEN** a branch and a tag both select the same commit
- **WHEN** push and manual-dispatch runs overlap for those refs
- **THEN** the runs execute the absence check and publication serially
- **AND** the later run observes the tag created by the first run and fails
  before building
