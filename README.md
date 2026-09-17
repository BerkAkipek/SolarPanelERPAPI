# Solar Panel ERP API (v1.0.0)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com)
[![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-336791.svg?logo=postgresql)](https://www.postgresql.org)
[![Alembic](https://img.shields.io/badge/Alembic-Migrations-orange.svg)](https://alembic.sqlalchemy.org)
[![Tests](https://img.shields.io/badge/pytest-320%20passed-success.svg)](https://docs.pytest.org)

An enterprise-grade, domain-driven REST API for solar photovoltaic (PV) module manufacturing and fulfillment operations. Built with **Python 3.12**, **FastAPI**, **PostgreSQL 17**, and **SQLAlchemy 2.x**.

---

## 1. The Core Business Capabilities

Version 1.0.0 provides definitive, transactional answers to the fundamental operational questions of a solar module plant:

```text
                               +-----------------------------+
                               |     Customer Sales Order    |
                               |    (e.g., 100x PV-550)      |
                               +-----------------------------+
                                              |
                                              v
                              +-------------------------------+
                              |     Fulfillment Analysis      |
                              | GET /sales-orders/{id}/...    |
                              +-------------------------------+
                                              |
                     +------------------------+------------------------+
                     |                                                 |
                     v                                                 v
        +-------------------------+                       +-------------------------+
        |     Ship from Stock     |                       |   Production Required   |
        |       (30 panels)       |                       |       (70 panels)       |
        +-------------------------+                       +-------------------------+
                                                                       |
                                                                       v
                                                          +-------------------------+
                                                          |  Feasibility Analysis   |
                                                          |  (BOM Explosion vs      |
                                                          |   Reservable Inventory) |
                                                          +-------------------------+
                                                                       |
                                      +--------------------------------+--------------------------------+
                                      |                                                                 |
                                      v                                                                 v
                         +--------------------------+                                      +--------------------------+
                         |     Shortage Detected    |                                      |   All Components Avail   |
                         |  (e.g., 20x Junction Box |                                      |   (can_produce = true)   |
                         |   isolated in QA-HOLD)   |                                      +--------------------------+
                         +--------------------------+                                                   |
                                      |                                                                 v
                                      | (Post component receipt)                           +--------------------------+
                                      +--------------------------------------------------->|  Atomic Production Order |
                                                                                           |  Release -> Start ->     |
                                                                                           |  Complete (Single Tx)    |
                                                                                           +--------------------------+
```

1. **Can we fulfill a customer order directly from warehouse stock?**
   - Evaluates uncommitted finished goods across eligible warehouse locations without altering inventory.
2. **Do we need production to satisfy unfulfilled demand?**
   - Automatically computes `ship_from_stock = min(ordered, available)` and `production_required = max(0, ordered - ship_from_stock)`.
3. **If production is required, do we have the feasible raw materials in stock to manufacture the missing panels?**
   - Explodes active Bill of Materials (BOM) recipes with exact decimal arithmetic and verifies component stock in reservable locations. Stock in quarantine (`QUARANTINE`), damaged (`DAMAGED`), or transit (`TRANSIT`) is strictly excluded.
4. **Can we execute production orders atomically?**
   - Consumes component reservations, issues raw materials, outputs finished modules, and completes the manufacturing order within a **single PostgreSQL transaction**.

---

## 2. Technology Stack

- **Backend Runtime**: Python 3.12+
- **API Framework**: FastAPI with Pydantic v2
- **Relational Database**: PostgreSQL 17
- **ORM & Driver**: SQLAlchemy 2.0 & psycopg 3
- **Schema Migrations**: Alembic
- **Testing**: pytest & FastAPI TestClient (320 tests executing against isolated PostgreSQL schemas)
- **Containerization**: Docker & Docker Compose

---

## 3. Quick Start

### 3.1. Configure Environment
```bash
make install
```
*Copies `.env.example` to `.env`. Default ports are `8000` for FastAPI and `5432` for PostgreSQL.*

### 3.2. Start Infrastructure
```bash
make up
make ps
```
*Starts PostgreSQL 17, automatically executes Alembic database migrations, and launches the FastAPI server.*

### 3.3. Populate Demo Data
```bash
make seed
```
*Seeds Solaria Dynamics Inc. (`X-Organization-ID: 1`), customer Helios Energy Solutions, PV-550 panel catalog, 5-component BOM recipe, warehouse stock, quarantine hold, and sales order `SO-2026-0001`.*

### 3.4. Run the Full Test Suite
```bash
make test
```
*Executes all 320 automated integration and behavior tests inside Docker Compose.*

### 3.5. Interactive Documentation
- **Standalone Swagger UI**: Open [`swagger.html`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/swagger.html) directly in any web browser.
- **Live Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **OpenAPI 3.1 JSON**: [`openapi.json`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/openapi.json) or [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## 4. The Canonical Solar Manufacturing Demo Scenario

The system includes a pre-configured, realistic solar manufacturing lifecycle scenario runnable via `make seed`:

### 4.1. The Setup
- **Organization**: Solaria Dynamics Inc. (ID: `1`)
- **Product**: `PV-550` (550W Half-Cell Monocrystalline Solar Panel, `REV-A`)
- **Active BOM Recipe** (for 1 module):
  - `CELL-M10` (Solar Cells): 144 EA
  - `GLASS-3.2` (Tempered Front Glass): 1 EA
  - `EVA-SHEET` (Encapsulant Film): 2 M²
  - `JB-1500` (1500V Split Junction Box): 1 EA
  - `FRAME-68` (Anodized Aluminum Frame): 1 EA
- **Physical Inventory**:
  - `FG-WH` (Finished Goods Warehouse): 30 panels `PV-550`
  - `RAW-WH` (Raw Warehouse): 12,000 cells, 100 glass, 200 EVA, 50 junction boxes, 100 frames
  - `QA-HOLD` (Quarantine Inspection Bay): **20 junction boxes `JB-1500`** *(Pending flash/potting test; strictly non-reservable!)*
- **Customer Demand**: Sales Order `SO-2026-0001` for **100 panels**.

### 4.2. End-to-End Walkthrough

```mermaid
sequenceDiagram
    autonumber
    actor User as Plant Manager / API Client
    participant Sales as Sales Module
    participant Prod as Production Module
    participant Inv as Inventory Ledger
    participant DB as PostgreSQL 17

    User->>Sales: POST /sales-orders/{id}/confirm
    Sales->>DB: Status -> CONFIRMED

    User->>Sales: GET /sales-orders/{id}/fulfillment-analysis
    Sales->>Inv: Query uncommitted stock
    Sales-->>User: ordered=100, ship_from_stock=30, production_required=70

    User->>Sales: GET /sales-orders/{id}/material-feasibility
    Sales->>Prod: Explode BOM for 70 panels
    Prod-->>Sales: Needs 10,080 cells, 70 glass, 140 EVA, 70 JB
    Sales->>Inv: Check reservable stock (RAW-WH)
    Note over Inv: 50 JB in RAW-WH; 20 in QA-HOLD are ignored!
    Sales-->>User: can_produce = false, shortage: 20x JB-1500

    User->>Inv: POST /inventory/movements (RECEIPT: +30 JB-1500 into RAW-WH)
    Inv->>DB: Append movement lines (Available JB = 80)

    User->>Prod: POST /production-orders (Create PO for 70 panels, DRAFT)
    Prod->>DB: Freezes material requirements snapshot

    User->>Prod: POST /production-orders/{id}/release
    Prod->>Inv: Reserve 10,080 cells, 70 glass, 140 EVA, 70 JB, 70 frames
    Inv->>DB: Inserts StockReservations (Status: ACTIVE)
    Prod->>DB: Order Status -> RELEASED

    User->>Prod: POST /production-orders/{id}/start
    Prod->>DB: Order Status -> IN_PROGRESS

    User->>Prod: POST /production-orders/{id}/complete (to_location_id: FG-WH)
    Note over DB: ATOMIC SINGLE-TRANSACTION EXECUTION
    Prod->>Inv: 1. Consume material reservations (ACTIVE -> CONSUMED)
    Prod->>Inv: 2. Post PRODUCTION_CONSUMPTION movements
    Prod->>Inv: 3. Post PRODUCTION_OUTPUT movement (+70 PV-550 into FG-WH)
    Prod->>DB: 4. Order Status -> COMPLETED

    User->>Sales: GET /sales-orders/{id}/fulfillment-analysis
    Sales-->>User: available=100, can_fulfill_entirely_from_stock = TRUE!
```

---

## 5. Complete REST API Reference

All business endpoints require the header `X-Organization-ID: <org_id>`. Decimal quantities are string-serialized (e.g. `"144.000000"`).

### 5.1. Product Definition (`/items`, `/boms`)
| Method | Path | Summary | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/items` | Create catalog item | Creates a finished panel, component, or raw material with unique SKU. |
| `GET` | `/items/{item_id}` | Get item details | Returns catalog item identity and base UOM. |
| `POST` | `/items/{item_id}/revisions` | Create item revision | Creates an immutable revision code (e.g., `REV-A`). |
| `GET` | `/items/{item_id}/revisions` | List item revisions | Lists revisions with BOM summaries. |
| `POST` | `/boms` | Create BOM recipe | Creates a versioned recipe header in `DRAFT` status. |
| `GET` | `/boms/{bom_id}` | Get BOM recipe | Retrieves recipe header and component lines. |
| `POST` | `/boms/{bom_id}/lines` | Add BOM line | Adds component revision and required quantity to a `DRAFT` BOM. |
| `POST` | `/boms/{bom_id}/activate` | Activate BOM recipe | Locks the recipe as `ACTIVE`. Once active, lines are strictly immutable. |

### 5.2. Inventory Ledger & Availability (`/inventory`)
| Method | Path | Summary | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/inventory/movements` | Post inventory movement | Posts double-entry append-only movement lines. Supports `Idempotency-Key`. |
| `GET` | `/inventory/items/{id}/on-hand` | Get physical on-hand stock | Returns physical $\text{Incoming} - \text{Outgoing}$ across all locations. |
| `GET` | `/inventory/items/{id}/availability` | Get available stock | Returns $\text{OnHand} - \text{ActiveReservations}$ across reservable locations only. |
| `POST` | `/inventory/reservations` | Create stock reservation | Allocates stock to a sales order line or production order material line. |
| `GET` | `/inventory/reservations/{id}` | Get reservation details | Retrieves reservation status and owner details. |
| `POST` | `/inventory/reservations/{id}/release` | Release reservation | Soft-releases reservation (`ACTIVE` $\rightarrow$ `RELEASED`), restoring stock. |
| `DELETE`| `/inventory/reservations/{id}` | Release reservation (alias)| Soft-releases reservation. Safe to retry. |
| `POST` | `/inventory/reservations/{id}/consume` | Consume reservation | Atomically marks reservation `CONSUMED` and posts stock issue. |

### 5.3. Sales Orders (`/sales-orders`)
| Method | Path | Summary | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/sales-orders` | Create sales order | Creates customer sales order in `DRAFT` status. |
| `GET` | `/sales-orders/{id}` | Get sales order details | Retrieves order header, customer, lines, and status. |
| `POST` | `/sales-orders/{id}/lines` | Add order line | Appends ordered revision, quantity, and unit price. |
| `POST` | `/sales-orders/{id}/confirm` | Confirm sales order | Transitions `DRAFT` $\rightarrow$ `CONFIRMED`. Validates revisions are active. |
| `POST` | `/sales-orders/{id}/cancel` | Cancel sales order | Cancels order. Requires active reservations to be released first. |
| `GET` | `/sales-orders/{id}/fulfillment-analysis` | Analyze order fulfillment | Read-only analysis calculating `ship_from_stock` vs `production_required`. |
| `GET` | `/sales-orders/{id}/material-feasibility` | Analyze material feasibility | Combines fulfillment analysis with BOM explosion to identify component shortages. |
| `POST` | `/sales-orders/{id}/production-orders` | Create production order | Creates manufacturing order tied to a sales order line. |

### 5.4. Production Planning & Orders (`/production`, `/production-orders`)
| Method | Path | Summary | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/production/boms/{id}/explode` | Explode BOM recipe | Pure domain arithmetic: returns component quantities for a batch. |
| `GET` | `/production/boms/{id}/feasibility` | Analyze BOM feasibility | Checks reservable component inventory and reports shortage quantities. |
| `POST` | `/production-orders` | Create production order | Creates order in `DRAFT` status with frozen material snapshot lines. |
| `GET` | `/production-orders/{id}` | Get production order | Retrieves order status and frozen material requirements. |
| `POST` | `/production-orders/{id}/release` | Release production order | Reserves all required component materials. Fails atomically if any is short. |
| `POST` | `/production-orders/{id}/start` | Start production order | Transitions `RELEASED` $\rightarrow$ `IN_PROGRESS`. |
| `POST` | `/production-orders/{id}/complete` | Complete production order | **Atomic transaction**: consumes reservations, issues raw materials, receipts finished goods, and marks order `COMPLETED`. |
| `POST` | `/production-orders/{id}/cancel` | Cancel production order | Cancels order and automatically releases all active material reservations. |

---

## 6. Standardized Error Handling

All error responses return a uniform JSON error envelope:
```json
{
  "detail": {
    "code": "insufficient_stock",
    "message": "Movement would consume more stock than available at the source location."
  }
}
```

| HTTP Status | Error Code | Example Trigger Condition |
| :---: | :--- | :--- |
| **404** | `item_not_found`, `revision_not_found`, `order_not_found` | Resource ID does not exist in the tenant organization. |
| **409** | `insufficient_stock` | Reservation or movement quantity exceeds available stock. |
| **409** | `idempotency_conflict` | Same `Idempotency-Key` provided with conflicting payload. |
| **409** | `bom_locked` | Attempted modification of an `ACTIVE` or `OBSOLETE` recipe. |
| **409** | `duplicate_sku`, `duplicate_order_number` | Unique key violation within tenant organization. |
| **422** | `invalid_movement_direction` | Locations provided do not match movement type rules. |
| **422** | `finished_good_required` | Attempted creation of a BOM for a component or raw material. |
| **422** | `bom_self_reference` | BOM contains its own finished good revision as a component. |
| **422** | `location_not_reservable` | Attempted reservation in quarantine, damaged, or transit location. |

---

## 7. Authoritative Documentation Suite

This repository maintains strictly **6 authoritative documents**:

1. [`AGENTS.md`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/AGENTS.md): Developer & AI Assistant architectural guidelines, domain principles, module boundaries, and invariant enforcement.
2. [`README.md`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/README.md): Primary system documentation, quickstart, demo scenario walkthrough, and REST API specification.
3. [`dbSchema.md`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/dbSchema.md): Comprehensive PostgreSQL database schema, Mermaid ERD, table data dictionary, views, triggers, and index strategies.
4. [`openapi.json`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/openapi.json): Standard OpenAPI 3.1.0 machine-readable API specification.
5. [`swagger.html`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/swagger.html): Standalone interactive Swagger UI documentation viewable directly in any browser.
6. [`manualTestPlan.md`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/manualTestPlan.md): Comprehensive manual testing guide with step-by-step curl requests and expected response envelopes.
