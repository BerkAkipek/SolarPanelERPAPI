# Architecture

The application is a modular monolith: one FastAPI process, one PostgreSQL database, and versioned Alembic migrations. This keeps the first cross-module flow transactional and easy to reason about.

Request direction: HTTP router -> request dependencies -> feature service -> SQLAlchemy -> PostgreSQL constraints/views -> response model.

## Boundaries

app.products owns items, revisions, BOMs, BOM lines, and activation. app.inventory owns movement posting and physical on-hand reads. app.dependencies owns organization scope and request database sessions. app.errors maps safe database and domain failures to stable HTTP errors. database/alembic owns migration ordering; database/migrations owns PostgreSQL DDL and invariants. tests assert public behavior.

## Dependency direction

Feature modules may depend on shared app models, schemas, dependencies, and errors. Shared code must not import features. Product definition does not call inventory. Later fulfillment orchestration will coordinate both in an application service.

Each request commits or rolls back one transaction. Inventory writes serialize by organization advisory lock. PostgreSQL is authoritative for relationships, quantities, history protections, and derived balance views. SQLAlchemy maps the schema; it does not create it.

Add a migration for every schema change. Do not edit an applied migration. Keep cross-module writes in one explicit service transaction and test missing references, conflicts, rollback, retries, and concurrent writes.
