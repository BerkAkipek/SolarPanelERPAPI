# AGENTS.md — Developer & AI Assistant Architecture Guide

This document is the authoritative guideline for software engineers and AI coding assistants working in the **Solar Panel ERP API** repository. All code modifications, refactorings, tests, and additions MUST strictly adhere to the principles and invariants detailed herein.

---

## 1. General Design Principles

| # | Principle | Rule |
| :---: | :--- | :--- |
| **1** | **Keep it simple** | Add complexity only when a real requirement justifies it. Prefer transparent modular design over distributed microservices. |
| **2** | **One source of truth** | Every business fact and rule has one authoritative owner. Physical inventory balances are derived from movements, never stored as mutable counters. |
| **3** | **Protect invariants** | Prevent invalid states with domain validation, Pydantic schemas, and PostgreSQL constraints/triggers. Never rely solely on application-layer checks. |
| **4** | **Explicit workflows** | Important state changes happen through explicit business operations (e.g. `activate`, `confirm`, `release`, `complete`), not arbitrary PUT/PATCH updates. |
| **5** | **Clear boundaries** | Modules have focused responsibilities, high cohesion, low coupling, and zero circular dependencies. |
| **6** | **Design for failure** | Use single-transaction boundaries, advisory locks, idempotency keys, and safe retries where correctness matters. |
| **7** | **Preserve important history** | Keep audit-worthy events and state changes (append-only movements, frozen material snapshots, soft releases) instead of overwriting or deleting. |
| **8** | **Make failures understandable** | Errors and logs must clearly explain what happened, why, and with which relevant inputs, returning stable machine-readable codes. |
| **9** | **Test behavior** | Test observable business behavior and public API contracts rather than private internal implementation details. |
| **10** | **Test failure paths** | Always test invalid input, conflicts, missing references, edge quantities, boundary cases, and concurrency rollbacks. |

---

## 2. Solar Panel ERP Domain Principles

| # | Principle | Meaning & Practical Implementation |
| :---: | :--- | :--- |
| **1** | **End-to-end traceability** | Trace raw material component revision $\rightarrow$ BOM $\rightarrow$ panel model $\rightarrow$ sales order $\rightarrow$ production run $\rightarrow$ warehouse stock. |
| **2** | **Single source of truth** | Master entities (`items`, `organizations`, `inventory_locations`, `business_partners`) have one canonical representation. |
| **3** | **Separate product from physical unit** | `Item` / `ItemRevision` describes *what something is* (e.g., PV-550 REV-A). Physical inventory movements and location balances describe *where and how many* exist physically. |
| **4** | **Lot + serial genealogy** | Component revisions and production order material links ensure any defective lot (e.g., junction box or EVA sheet) is traceable to every affected module. |
| **5** | **Quality is part of the domain** | Inventory locations carry explicit types (`QUARANTINE`, `DAMAGED`, `WAREHOUSE`). Quarantined or damaged stock is never promised to customers or manufacturing. |
| **6** | **Projects and manufacturing are separate flows** | Assembling 5,000 PV modules in a plant and installing a 2 MW field project are different lifecycles sharing inventory. |
| **7** | **Inventory has state, not just quantity** | 100 panels can mean 60 available, 20 reserved, 10 quarantined, 5 damaged, and 5 in transit. |
| **8** | **Financial events follow physical events** | Physical inventory movements (`RECEIPT`, `SHIPMENT`, `PRODUCTION_CONSUMPTION`, `PRODUCTION_OUTPUT`) precede cost/revenue recognition. |
| **9** | **History beats overwriting** | Revisions are immutable. BOM recipes freeze upon activation. Physical movement lines and material snapshot lines cannot be modified or deleted. |
| **10** | **Critical transactions are auditable** | Every movement records `occurred_at`, `created_at`, `movement_type`, and an optional `reference`. |
| **11** | **Workflow is explicit** | Controlled state transitions: Sales Orders (`DRAFT` $\rightarrow$ `CONFIRMED` $\rightarrow$ `CANCELLED`), Production Orders (`DRAFT` $\rightarrow$ `RELEASED` $\rightarrow$ `IN_PROGRESS` $\rightarrow$ `COMPLETED`), Reservations (`ACTIVE` $\rightarrow$ `RELEASED` / `CONSUMED`). |
| **12** | **Modules share a common core** | Product catalog, inventory ledger, sales, and production integrate through shared domain models (`ItemRevision`, `StockReservation`) without schema duplication. |

---

## 3. Technology Stack & Architectural Decisions

| Layer | Technology | Usage & Standards |
| :--- | :--- | :--- |
| **Runtime** | Python 3.12+ | Strongly-typed Python with type annotations and `dataclasses`. |
| **Framework** | FastAPI | REST API routing, dependency injection (`DatabaseSession`, `OrganizationID`), OpenAPI documentation. |
| **Database** | PostgreSQL 17 | Relational persistence, triggers, advisory locks, derived balance views (`inventory_on_hand`, `inventory_availability`). |
| **ORM & Driver** | SQLAlchemy 2.x & psycopg 3 | 2.0-style `select()` syntax, transactional session management. |
| **Migrations** | Alembic | Sequential schema migrations (`database/alembic/versions/`). |
| **Validation** | Pydantic v2 | Strict request/response schemas with `extra='forbid'`, positive decimal constraints. |
| **Testing** | pytest | 320 automated unit and integration tests executing against isolated PostgreSQL schemas. |
| **Containerization** | Docker & Compose | Multi-container setup: `db`, `migrate`, `api`, `test`. |

### Architectural Decisions Records (ADRs)
- **ADR 0001 (Modular Monolith)**: A single FastAPI service process running over one PostgreSQL database. Feature boundaries are cleanly separated by package (`products`, `inventory`, `production`, `sales`). Cross-domain operations share a single transactional database session for atomicity.
- **ADR 0002 (PostgreSQL Ledger Invariants)**: Physical on-hand stock and available stock are derived in real-time views from append-only movement lines and active reservations. Triggers enforce non-negative balances and location validity.
- **ADR 0003 (Explicit BOM Activation)**: Recipes are authored in `DRAFT`, lines are added, and the BOM is explicitly locked as `ACTIVE`. Active BOMs cannot be edited or deleted.
- **ADR 0004 (Idempotent Physical Postings)**: Inventory movement creation accepts an `Idempotency-Key` header and calculates a SHA-256 request fingerprint to guarantee safe retries without duplicate inventory posting.

---

## 4. Codebase Structure & Module Boundaries

```text
solarPanelERPAPI/
├── app/
│   ├── main.py                 # FastAPI application factory, metadata, routers, global error handlers
│   ├── config.py               # Database URL resolution and environment settings
│   ├── db.py                   # SQLAlchemy engine, sessionmaker, and get_session dependency
│   ├── dependencies.py         # Request dependencies: DatabaseSession, OrganizationID, PathID
│   ├── errors.py               # DomainError definition and DBAPI constraint error mapping
│   ├── models.py               # Shared ORM base and Organization model
│   ├── schemas.py              # Shared Pydantic types (Quantity, Decimal serialization)
│   ├── products/               # Product Definition Module
│   │   ├── models.py           # Item, ItemRevision, BOM, BOMLine
│   │   ├── schemas.py          # ItemCreate, RevisionCreate, BOMCreate, BOMLineCreate
│   │   ├── router.py           # /items, /boms endpoints
│   │   └── service.py          # Product catalog queries and explicit BOM activation
│   ├── inventory/              # Inventory Ledger Module
│   │   ├── models.py           # InventoryLocation, InventoryMovement, InventoryMovementLine, StockReservation
│   │   ├── schemas.py          # MovementCreate, ReservationCreate, OnHandRead, AvailabilityRead
│   │   ├── router.py           # /inventory/movements, /inventory/reservations, /inventory/items/*
│   │   └── service.py          # Double-entry posting, advisory locking, reservation transitions
│   ├── production/             # Production & Planning Module
│   │   ├── domain.py           # Pure domain math: explode_bom, evaluate_material_feasibility
│   │   ├── models.py           # ProductionOrder, ProductionOrderMaterial (snapshot requirements)
│   │   ├── schemas.py          # ProductionOrderCreate, BOMExplosionRead, MaterialFeasibilityRead
│   │   ├── router.py           # /production/boms/*, /production-orders/*
│   │   └── service.py          # Production order lifecycle, atomic release, atomic complete
│   └── sales/                  # Sales & Demand Module
│       ├── models.py           # BusinessPartner, SalesOrder, SalesOrderLine
│       ├── schemas.py          # SalesOrderCreate, FulfillmentAnalysisRead, FeasibilityRead
│       ├── router.py           # /sales-orders/*
│       └── service.py          # Sales order lifecycle, read-only fulfillment analysis
├── database/
│   ├── Dockerfile              # Container definition for API, migration, and test services
│   ├── requirements.txt        # Runtime Python dependencies
│   ├── alembic/                # Alembic migration configuration and environment
│   └── migrations/             # Idempotent SQL migration files (0001_v1 to 0005_production)
├── scripts/
│   └── seed_demo.py            # Comprehensive solar plant demo scenario seed script
├── tests/                      # Full test suite (320 tests across isolated schemas)
├── compose.yaml                # Docker Compose orchestration
├── Makefile                    # Operational CLI targets (up, down, migrate, seed, test, clean)
├── openapi.json                # Complete OpenAPI 3.1 specification
├── swagger.html                # Standalone interactive Swagger UI viewer
├── dbSchema.md                 # Authoritative database documentation and ERD
├── manualTestPlan.md           # Step-by-step acceptance test scenarios
└── README.md                   # Primary system documentation
```

### Dependency Direction Rules
1. **HTTP Routers** call **Feature Services** and depend on **Request Dependencies**.
2. **Feature Services** execute domain rules and interact with **SQLAlchemy Models**.
3. **Pure Domain Functions** (`app/production/domain.py`) MUST remain completely free of database, ORM, or HTTP dependencies.
4. **Cross-Module Interactions**:
   - `app.sales.service` may query `app.inventory.service` (for stock availability) and `app.production.service` (for material feasibility).
   - `app.production.service` may coordinate with `app.inventory.service` (for reservation creation and movement posting).
   - Feature modules MUST NEVER circular-import each other.
   - Cross-module business workflows (such as completing a production order) execute within a single shared `Session` transaction.

---

## 5. Domain Invariants & Rules of Enforcement

### 5.1. Reservable Locations Invariant
- **Rule**: Only active locations of type `WAREHOUSE`, `BIN`, and `PRODUCTION` are eligible to promise stock for sales orders or reserve components for manufacturing.
- **Rule**: `QUARANTINE`, `DAMAGED`, and `TRANSIT` locations are strictly non-reservable.
- **Enforcement**:
  - Derived view `inventory_availability` excludes non-reservable locations.
  - PostgreSQL trigger `validate_stock()` raises `23514` (`location_not_reservable`) if an allocation is attempted on a non-reservable location.

### 5.2. Option A Traceability Invariant
- **Rule**: Stock reservations require exactly one owner: either `sales_order_line_id` (finished goods allocated to customer demand) OR `production_order_material_id` (raw components allocated to manufacturing).
- **Enforcement**: PostgreSQL check constraint `CHECK (num_nonnulls(sales_order_line_id, production_order_material_id) = 1)`.

### 5.3. Explicit State Machines
- **Sales Orders**: `DRAFT` $\xrightarrow{\text{confirm}}$ `CONFIRMED` $\xrightarrow{\text{cancel}}$ `CANCELLED`.
- **Stock Reservations**: `ACTIVE` $\xrightarrow{\text{release}}$ `RELEASED` OR `ACTIVE` $\xrightarrow{\text{consume}}$ `CONSUMED`.
  - Reservations are NEVER physically deleted; soft release preserves audit history.
  - Terminal statuses (`RELEASED`, `CONSUMED`) cannot transition to any other status.
- **Production Orders**: `DRAFT` $\xrightarrow{\text{release}}$ `RELEASED` $\xrightarrow{\text{start}}$ `IN_PROGRESS` $\xrightarrow{\text{complete}}$ `COMPLETED` (or $\xrightarrow{\text{cancel}}$ `CANCELLED`).
  - Creation in `DRAFT` creates frozen material requirement lines without placing reservations.
  - Releasing an order attempts to reserve all required materials; if any component has insufficient available stock, the entire release fails (`409 insufficient_stock`) and the order stays `DRAFT`.
  - Completing an order is an atomic transaction executing 4 steps simultaneously: consumes reservations, issues raw materials, outputs finished goods, and sets status to `COMPLETED`.

### 5.4. High-Precision Decimals
- All quantities in the database are `NUMERIC(18,6)`.
- In Python, quantities are represented as `Decimal` instances.
- In JSON payloads and responses, quantities are stringified decimals (e.g., `"144.000000"`) to prevent floating-point loss in client applications.
- BOM explosion arithmetic uses exact formula with `ROUND_HALF_UP` to 6 decimal places:
  $$\text{required} = \operatorname{round}\left(\frac{\text{production\_qty} \times \text{line\_qty}}{\text{bom\_output\_qty}}, 6\right)$$

---

## 6. Error Handling & Exception Patterns

All errors raised by the API return a uniform JSON envelope:
```json
{
  "detail": {
    "code": "insufficient_stock",
    "message": "Insufficient available stock at location RAW-WH."
  }
}
```

### Guidelines for Handling Errors
1. Raise `DomainError(status_code, code, message)` for business logic validation failures.
2. PostgreSQL constraint violations and trigger exceptions are automatically caught by `database_error_handler` in `app/errors.py` and mapped via `CONSTRAINT_ERRORS` to stable HTTP status codes (404, 409, 422).
3. Never expose raw SQL statements, database credentials, or unhandled tracebacks to API clients.

---

## 7. Testing Standards & Execution

- **Isolation**: Integration tests run against a real PostgreSQL instance using dynamically generated schemas (`test_<module>_<uuid>`). Alembic migrations are executed inside each isolated schema at fixture setup.
- **Docker Compose Test Service**: The authoritative test command is:
  ```bash
  docker compose --profile test run --rm test
  ```
- **Coverage Requirements**:
  - Pure domain functions (`explode_bom`, `evaluate_material_feasibility`).
  - PostgreSQL trigger and constraint enforcement (negative stock prevention, quarantine exclusion, locked BOM protection).
  - Transactional rollback verification on error.
  - Idempotent retry behavior.
  - Full end-to-end manufacturing and fulfillment lifecycle (`tests/test_v1_e2e.py`).
