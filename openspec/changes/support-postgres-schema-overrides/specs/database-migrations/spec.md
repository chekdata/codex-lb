## ADDED Requirements

### Requirement: Migration paths honor an optional PostgreSQL application schema

When `database_url` resolves to PostgreSQL and `database_postgres_schema` is a
non-empty string, every sync migration path MUST operate with a PostgreSQL
search path containing only `<configured-schema>`. This requirement applies to the
Alembic environment, startup migration upgrade path, startup drift check,
`wait-for-head`, and `wait-for-connection` / schema-inspection helpers that use
the shared sync connection factories. When the setting is omitted or empty,
existing migration behavior MUST remain unchanged.

#### Scenario: Startup migrations run inside the configured schema

- **GIVEN** `database_url` resolves to PostgreSQL
- **AND** `database_postgres_schema = "codex_lb_prod"`
- **WHEN** startup migration or drift-check code opens a sync PostgreSQL
  connection
- **THEN** the connection uses a migration-only `search_path = "codex_lb_prod"`
- **AND** Alembic upgrades and ORM drift checks resolve unqualified schema
  objects inside `codex_lb_prod`

#### Scenario: Shared-schema installs do not require public duplicates

- **GIVEN** PostgreSQL objects exist only in `codex_lb_prod`
- **AND** `database_postgres_schema = "codex_lb_prod"`
- **WHEN** `wait-for-head` or another schema-state check runs after migrations
- **THEN** the check succeeds against `codex_lb_prod`
- **AND** it does not report missing tables solely because `public` is empty

### Requirement: Migration bootstrap creates the configured PostgreSQL schema before reading schema state

When `database_url` resolves to PostgreSQL and `database_postgres_schema` is a
non-empty string other than `public`, migration and schema-inspection paths
MUST ensure that schema exists before they read Alembic state or run upgrades.
This requirement applies to startup migration entrypoints, Alembic online
migrations, drift checks, and migration wait helpers that use the shared sync
connection factories. If the database user cannot create schemas, the failure
MUST surface directly instead of silently falling back to `public`.

#### Scenario: First shared-schema install does not reuse public Alembic state

- **GIVEN** `database_url` resolves to PostgreSQL
- **AND** `database_postgres_schema = "codex_lb_prod"`
- **AND** `codex_lb_prod` does not exist yet
- **WHEN** startup migration or Alembic online migration begins
- **THEN** codex-lb creates `codex_lb_prod` before setting `search_path`
- **AND** subsequent Alembic state reads and upgrades resolve inside
  `codex_lb_prod`

#### Scenario: Public migration state cannot mask an uninitialized application schema

- **GIVEN** `public.alembic_version` is already at the current head
- **AND** `database_postgres_schema = "codex_lb_prod"`
- **AND** `codex_lb_prod` has no Alembic version table
- **WHEN** codex-lb inspects or upgrades the configured schema
- **THEN** it treats `codex_lb_prod` as uninitialized
- **AND** it creates and migrates tables in `codex_lb_prod` instead of reusing
  the migration state from `public`
- **AND** PostgreSQL catalog probes for enum types resolve only against
  `codex_lb_prod`
