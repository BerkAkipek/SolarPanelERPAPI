# Solar Panel ERP API (v1.0.0 - FROZEN)

Enterprise-grade FastAPI and PostgreSQL backend for solar panel manufacturing and fulfillment operations.

---

## The V1 Core Business Question

Version 1 solves the core operational dilemma of a solar module manufacturer:
> **"Given a sales order, can we fulfill it from stock, do we need production, and if production is needed, do we have the required materials?"**

---

## Technology Stack

- **Language & Runtime**: Python 3.12+
- **API Framework**: FastAPI & Pydantic v2
- **Database**: PostgreSQL 17
- **ORM & Migrations**: SQLAlchemy 2.x & Alembic
- **Testing**: pytest & FastAPI TestClient (205 integration tests across isolated schemas)
- **Containerization**: Docker & Docker Compose
- **Continuous Integration**: GitHub Actions (`.github/workflows/ci.yml`)

---

## Quick Start

### 1. Configure
```bash
make install
```
Creates `.env` from `.env.example`. Review settings (default port 8000 for API, 5432 for Postgres).

### 2. Start Services
```bash
make up
make ps
```
Starts PostgreSQL, automatically runs Alembic migrations, and launches the FastAPI service.

### 3. Seed Demo Data
```bash
make seed
```
Populates the system with the canonical solar manufacturing demo scenario:
- **Organization**: Solaria Dynamics Inc. (`X-Organization-ID: 1`)
- **Customer**: Helios Energy Solutions
- **Product**: `PV-550` (550W Module) with active BOM recipe (144 cells, 1 front glass, 2 EVA sheets, 1 junction box, 1 frame).
- **Physical Inventory**: 30 finished panels in warehouse, 12,000 cells, 50 junction boxes in warehouse, 20 junction boxes quarantined in QA bay.
- **Sales Order**: `SO-2026-0001` demanding 100 panels.
- **Fulfillment Outcome**: 30 from stock, 70 production required, shortage of 20 junction boxes (quarantine stock isolated!).

### 4. Run Automated Tests
```bash
make test
```
Executes the full test suite (205 tests) covering:
- Unit & domain math (BOM explosion, decimal rounding)
- PostgreSQL constraints & triggers (balance invariants, status protections)
- Rollback & transactional atomicity
- Concurrency & advisory locks
- Idempotency & retries
- Complete V1 end-to-end manufacturing and fulfillment lifecycle (`tests/test_v1_e2e.py`)

---

## The V1 Manufacturing & Fulfillment Lifecycle

```
1. Sales Order (100x PV-550)
   ├── Status: DRAFT ──(POST /confirm)──> CONFIRMED
   │
2. Fulfillment Analysis (GET /sales-orders/{id}/fulfillment-analysis)
   ├── Ordered: 100 | Available in Stock: 30
   └── Ship from Stock: 30 | Production Required: 70
       │
3. Feasibility Analysis (GET /sales-orders/{id}/material-feasibility)
   ├── CELL-M10:  Req 10,080 | Avail 12,000 | Shortage: 0
   ├── GLASS-3.2: Req 70     | Avail 100    | Shortage: 0
   ├── EVA-SHEET: Req 140    | Avail 200    | Shortage: 0
   ├── JB-1500:   Req 70     | Avail 50     | Shortage: 20  <-- (20 in QA-HOLD quarantine ignored!)
   └── Outcome: can_produce = FALSE
       │
4. Component Delivery (POST /inventory/movements)
   └── Receive 30x JB-1500 into RAW-WH ──> Shortage resolved (can_produce = TRUE)
       │
5. Production Order Execution
   ├── POST /production-orders (Status: DRAFT)
   ├── POST /production-orders/{id}/release (Status: RELEASED, reserves 10,080 cells, 70 JB, etc.)
   ├── POST /production-orders/{id}/start (Status: IN_PROGRESS)
   └── POST /production-orders/{id}/complete (ATOMIC TRANSACTION):
       ├── 1. Transitions material reservations -> CONSUMED
       ├── 2. Posts PRODUCTION_CONSUMPTION movements for raw materials
       ├── 3. Posts PRODUCTION_OUTPUT movement (+70 PV-550 into FG-WH)
       └── 4. Marks production order COMPLETED
           │
6. Fulfillment Verification
   └── Re-analyze: Available in stock = 100 | Production required = 0 | can_fulfill_entirely_from_stock = TRUE!
```

---

## Interactive Documentation & OpenAPI

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **OpenAPI JSON**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

> **Tenant Isolation**: Business endpoints require the `X-Organization-ID` header.
> **Quantities**: High-precision decimal quantities are passed and returned as JSON strings (`NUMERIC(18,6)`).
> **Idempotency**: Ledger postings and executions accept `Idempotency-Key` headers.

---

## Core V1 Capabilities & Endpoints

| Capability | Endpoints | Description |
| --- | --- | --- |
| **Product definition** | `POST /items`, `GET /items/{id}`<br>`POST /items/{id}/revisions`, `GET /items/{id}/revisions`<br>`POST /boms`, `POST /boms/{id}/lines`, `POST /boms/{id}/activate` | Item catalog, immutable revisions, and versioned BOM recipes. |
| **Inventory ledger** | `POST /inventory/movements`<br>`GET /inventory/items/{revision_id}/on-hand` | Double-entry physical ledger with location tracking and non-negative balance triggers. |
| **Stock availability & reservations** | `GET /inventory/items/{revision_id}/availability`<br>`POST /inventory/reservations`<br>`POST /inventory/reservations/{id}/release`<br>`POST /inventory/reservations/{id}/consume` | Available stock calculation (`OnHand - ActiveReservations`) and Option A reservation genealogy. |
| **Sales orders** | `POST /sales-orders`, `GET /sales-orders/{id}`<br>`POST /sales-orders/{id}/lines`<br>`POST /sales-orders/{id}/confirm`<br>`POST /sales-orders/{id}/cancel` | Customer demand entry with explicit lifecycle transitions. |
| **Fulfillment analysis** | `GET /sales-orders/{id}/fulfillment-analysis` | Read-only analysis calculating `ship_from_stock` vs `production_required`. |
| **BOM explosion** | `GET /production/boms/{id}/explode` | Pure domain calculation determining exact component requirements. |
| **Material feasibility** | `GET /production/boms/{id}/feasibility`<br>`GET /sales-orders/{id}/material-feasibility` | Feasibility analysis checking component stock and reporting shortages. |
| **Production orders** | `POST /production-orders`, `GET /production-orders/{id}`<br>`POST /sales-orders/{id}/production-orders` | Manufacturing order creation snapshotting recipes in `DRAFT`. |
| **Production execution** | `POST /production-orders/{id}/release`<br>`POST /production-orders/{id}/start`<br>`POST /production-orders/{id}/complete`<br>`POST /production-orders/{id}/cancel` | Atomic manufacturing execution: consumes material reservations, posts consumption movements, and outputs finished modules in a single transaction. |

---

## Standardized Error Envelopes

All error responses return a standardized JSON structure:
```json
{
  "detail": {
    "code": "insufficient_stock",
    "message": "Insufficient available stock to reserve component 5 for production order 1."
  }
}
```

| HTTP Status | Error Code | Example Trigger |
| :---: | :--- | :--- |
| **404** | `order_not_found`, `revision_not_found` | Resource ID does not exist in tenant organization. |
| **409** | `insufficient_stock` | Attempted reservation exceeds available stock. |
| **409** | `invalid_order_status`, `order_cancelled` | Attempted transition violates business state machine. |
| **409** | `duplicate_idempotency_key` | Same idempotency key used with differing payload. |
| **422** | `empty_order`, `missing_location` | Request payload fails domain validation rules. |

---

## Project Documentation

- [Architecture & Monolith Design](file:///C:/Users/BERK/workSpace/solarPanelERPAPI/docs/architecture.md)
- [Domain Model & Relationships](file:///C:/Users/BERK/workSpace/solarPanelERPAPI/docs/domain-model.md)
- [Fulfillment Workflow](file:///C:/Users/BERK/workSpace/solarPanelERPAPI/docs/fulfillment.md)
- [Inventory Ledger & Availability](file:///C:/Users/BERK/workSpace/solarPanelERPAPI/docs/inventory.md)
- [Production & Execution](file:///C:/Users/BERK/workSpace/solarPanelERPAPI/docs/production.md)
- [V2 Roadmap & Backlog](file:///C:/Users/BERK/workSpace/solarPanelERPAPI/docs/v2-backlog.md)
