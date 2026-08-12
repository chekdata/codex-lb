## ADDED Requirements

### Requirement: Helm external PostgreSQL installs support an optional schema override

The Helm chart MUST expose one optional `config.databasePostgresSchema` value
for PostgreSQL installs that reuse a shared database. When set, the rendered
runtime workload, migration job, and startup database guard init containers
MUST all receive `CODEX_LB_DATABASE_POSTGRES_SCHEMA` with the same value. When
unset, the chart MUST preserve the current no-override behavior.

#### Scenario: Shared PostgreSQL schema is wired consistently

- **WHEN** the chart renders with `postgresql.enabled=false`
- **AND** `config.databasePostgresSchema=codex_lb_prod`
- **THEN** the main workload receives
  `CODEX_LB_DATABASE_POSTGRES_SCHEMA=codex_lb_prod`
- **AND** the migration job receives the same environment variable
- **AND** startup database init containers receive the same environment variable

#### Scenario: Default installs remain unchanged

- **WHEN** the chart renders without `config.databasePostgresSchema`
- **THEN** `CODEX_LB_DATABASE_POSTGRES_SCHEMA` is not emitted
- **AND** existing dedicated-database installs remain unchanged
