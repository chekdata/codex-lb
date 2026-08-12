## ADDED Requirements

### Requirement: PostgreSQL runtime sessions honor an optional application schema

When `database_url` resolves to PostgreSQL and `database_postgres_schema` is a
non-empty string, the application MUST configure every runtime SQLAlchemy
asyncpg connection with a PostgreSQL search path of `<configured-schema>,public`.
This requirement applies to the request-path engine, the optional background
engine, and any other runtime PostgreSQL async engine created through the
shared engine helper. When the setting is omitted or empty, PostgreSQL runtime
behavior MUST remain unchanged.

#### Scenario: Runtime asyncpg connections search the configured schema first

- **GIVEN** `database_url` uses `postgresql+asyncpg://`
- **AND** `database_postgres_schema = "codex_lb_prod"`
- **WHEN** the application opens a new runtime PostgreSQL connection
- **THEN** the connection uses `search_path = "codex_lb_prod",public`
- **AND** unqualified table reads and writes resolve to `codex_lb_prod` before
  `public`

#### Scenario: Omitted schema preserves existing runtime behavior

- **GIVEN** `database_url` uses `postgresql+asyncpg://`
- **AND** `database_postgres_schema` is omitted or empty
- **WHEN** the application opens a new runtime PostgreSQL connection
- **THEN** no search-path override is configured

### Requirement: Durable bridge table guards honor the active PostgreSQL schemas

When durable-bridge readiness or cleanup code checks whether the bridge tables
exist on PostgreSQL, it MUST resolve table presence from the connection's
currently active non-temporary schemas instead of assuming `public`. This keeps
shared-database installs compatible with a schema-specific search path while
preserving existing SQLite behavior.

#### Scenario: Non-public schema tables satisfy the durable-bridge guard

- **GIVEN** the database backend is PostgreSQL
- **AND** the active search path includes `codex_lb_prod,public`
- **AND** the required durable-bridge tables exist in `codex_lb_prod`
- **WHEN** the durable-bridge table guard runs
- **THEN** it reports that the tables are present
- **AND** it does not require duplicate copies in `public`
