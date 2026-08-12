# Tasks: support-postgres-schema-overrides

## 1. Runtime and migration wiring

- [x] 1.1 Add one normalized `database_postgres_schema` setting for shared
      PostgreSQL database installs
- [x] 1.2 Configure asyncpg runtime engines to apply `<schema>,public` when the
      setting is present
- [x] 1.3 Apply a schema-only PostgreSQL search path to sync migration, Alembic,
      and startup schema-check connections
- [x] 1.3.1 Create the configured PostgreSQL schema before migration/bootstrap
      state reads so first installs do not fall back to `public`
- [x] 1.3.2 Scope migration state and Alembic's version table to the configured
      schema without the runtime `public` fallback
- [x] 1.3.3 Commit PostgreSQL schema setup before Alembic owns transaction
      boundaries and scope historical enum catalog probes to the visible type
- [x] 1.4 Make durable-bridge table guards resolve against the active
      PostgreSQL schemas instead of hard-coding `public`

## 2. Install contract and docs

- [x] 2.1 Expose the schema setting through Helm values, templates, and example
      overlays
- [x] 2.2 Document shared PostgreSQL installs in the Kubernetes guide and
      `.env.example`
- [x] 2.3 Regenerate the checked-in settings reference page

## 3. Verification

- [x] 3.1 Add focused unit coverage for runtime engine kwargs, migration search
      path helpers, Helm rendering, and durable-bridge schema detection
- [x] 3.2 Run focused unit tests for the new schema wiring
- [x] 3.3 Run `openspec validate --specs`
