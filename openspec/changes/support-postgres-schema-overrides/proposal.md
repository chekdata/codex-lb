# Change: support-postgres-schema-overrides

## Why

CHEK's production deployment reuses an existing PostgreSQL instance instead of
provisioning a dedicated database per application. `codex-lb` currently assumes
the PostgreSQL session operates in `public`, so runtime queries, migration
checks, and durable-bridge table guards all target the default schema. In a
shared database that can leak into the wrong schema, report missing tables even
after successful migrations, or require operators to create one database per
deployment just to isolate objects.

## What Changes

- Add one explicit `database_postgres_schema` setting that, when configured,
  pins PostgreSQL runtime sessions to `<schema>,public` and migration paths to
  the configured schema only.
- Make Alembic, startup drift checks, migration waits, and durable-bridge table
  detection honor the configured active PostgreSQL schemas instead of assuming
  `public`.
- Ensure migration/bootstrap paths create the configured PostgreSQL schema on
  first use when the database user is allowed to do so, so shared-database
  installs do not accidentally fall back to `public` Alembic state.
- Scope migration search paths and Alembic's version table to the configured
  schema only, while retaining the documented `public` fallback for runtime
  application queries.
- Surface the setting through the Helm chart and environment examples so shared
  database installs can keep the app, migration job, and readiness guards on
  the same schema contract.
- Document the shared-database installation path and cover the new wiring with
  focused unit tests.

## Impact

- Affected specs: `database-backends`, `database-migrations`,
  `deployment-installation`
- Affected code: settings, database session wiring, migration helpers,
  durable-bridge schema detection, Helm values/templates, and documentation
- Existing dedicated-database installs remain unchanged because the default is
  unset and preserves PostgreSQL's normal search path
