from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from app.errors import DomainError, database_error_handler, domain_error_handler
from app.products.router import router as products_router
from app.inventory.router import router as inventory_router
from app.sales.router import router as sales_router
from app.production.router import production_orders_router, router as production_router


API_DESCRIPTION = """
# Solar Panel ERP API (v1.0.0)

A robust, enterprise-grade REST API for solar panel manufacturing and fulfillment operations.

## Core V1 Business Capabilities
Version 1 answers the central operational questions of a solar module plant:
1. **Given a customer sales order, can we fulfill it directly from warehouse stock?**
2. **Do we need production to satisfy unfulfilled demand?**
3. **If production is required, do we have the feasible raw materials in stock to manufacture the missing panels?**
4. **Can we execute production orders atomically, reserving and consuming raw components and outputting finished modules?**

---

## Architectural Invariants & Domain Principles

- **One Source of Truth**: Physical stock quantities are derived exclusively from immutable, append-only `inventory_movements` and `inventory_movement_lines`. No mutable counter columns exist.
- **Stock Availability**: `Available = OnHand - ActiveReservations`. Reservable locations are strictly active locations of type `WAREHOUSE`, `BIN`, and `PRODUCTION`. Quarantine (`QUARANTINE`), damaged (`DAMAGED`), and transit (`TRANSIT`) locations are never promised to sales orders or production feasibility.
- **Option A Traceability**: Component reservations belong directly to production order material lines (`production_order_material_id`), preserving end-to-end genealogy.
- **Explicit Workflows**:
  - Sales Orders: `DRAFT` ➔ `CONFIRMED` ➔ `CANCELLED`
  - Stock Reservations: `ACTIVE` ➔ `RELEASED` or `CONSUMED`
  - Production Orders: `DRAFT` ➔ `RELEASED` ➔ `IN_PROGRESS` ➔ `COMPLETED` (or `CANCELLED`)
- **Atomic Execution**: Completing a production order executes four interrelated domain events in a single PostgreSQL transaction: consumes reservations, posts raw material consumption movements, posts finished goods output movements, and marks the order `COMPLETED`. Any failure triggers an automatic full rollback.
"""

TAGS_METADATA = [
    {
        "name": "Product definition",
        "description": "Product master data: finished solar panels, raw components, immutable revisions, and active BOM recipes.",
    },
    {
        "name": "Inventory ledger",
        "description": "Double-entry physical stock movements, real-time on-hand balances, stock availability, and stock reservations.",
    },
    {
        "name": "Sales orders",
        "description": "Customer order entry, explicit confirmation lifecycle, read-only fulfillment analysis, and order-level material feasibility.",
    },
    {
        "name": "Production",
        "description": "Pure BOM explosion arithmetic and material feasibility calculations against current reservable inventory.",
    },
    {
        "name": "Production orders",
        "description": "Manufacturing order creation, release (with material reservation), in-progress tracking, and atomic execution.",
    },
]

app = FastAPI(
    title="Solar Panel ERP API",
    version="1.0.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
)

app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(DBAPIError, database_error_handler)
app.include_router(products_router)
app.include_router(inventory_router)
app.include_router(sales_router)
app.include_router(production_router)
app.include_router(production_orders_router)





@app.get("/health")
def health():
    return {"status": "ok"}
