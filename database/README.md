# Production V1 database

PostgreSQL 17.11 runs in Docker Compose with a persistent named volume.
Alembic applies the versioned schema using SQLAlchemy 2.x and psycopg.
The FastAPI app implements product definition (items, revisions, BOMs, and activation).
See [Product definition API](../docs/product-definition.md) for endpoints and examples.
It also implements atomic inventory movements and physical on-hand queries; see
[Inventory ledger API](../docs/inventory-ledger.md). Reservation, sales, production,
and shipment API operations are later steps.

## Run

A generated local password is in the ignored `.env` file. On a fresh checkout,
copy `.env.example` to `.env` and replace the password.

```powershell
docker compose up -d --build
docker compose ps -a
docker compose logs migrate
docker compose --profile test run --rm --build test
```

The database should be healthy and the one-shot migration container should exit
with code 0. Re-running the migration is safe: Alembic tracks the installed version.
Use new Alembic revisions for future changes; do not edit an applied migration.

Connection settings:

| Setting | Value |
| --- | --- |
| Host | localhost |
| Port | 5432 (override POSTGRES_PORT in .env) |
| Database | solar_erp |
| User | solar_erp |
| Password | POSTGRES_PASSWORD in .env |

Open a SQL console:

```powershell
docker compose exec db psql -U solar_erp -d solar_erp
```

`docker compose stop` stops containers while keeping data. The named volume keeps
data across container recreation. Changing the password environment variable
does not change the password of an already initialized PostgreSQL database.

This is a local development database: its owner account is not a restricted
production API role. Organization foreign keys prevent cross-organization
relationships; they do not implement authentication, authorization, or row-level
read isolation.

## Schema

Arrows mean parent to child (one to many). Repeated boxes refer to the same table.
Every business table belongs to an organization, including child records.

```text
+-------------------+
| organizations     |
+-------------------+
    | owns all records below
    |
    +--> +-------------------+     +-------------------+
    |    | business_partners |---->| sales_orders      |
    |    +-------------------+     +-------------------+
    |                                       |
    |                              +-------------------+
    |                              | sales_order_lines |
    |                              +-------------------+
    |                                  |           |
    |                                  v           v
    |                     +-------------------+  +--------------------+
    |                     | production_orders |  | stock_reservations |
    |                     +-------------------+  +--------------------+
    |                              |                       ^
    |                              v                       |
    |                     +----------------------------+   |
    |                     | production_order_materials |---+
    |                     +----------------------------+
    |
    +--> +-------+     +----------------+
    |    | items |---->| item_revisions |
    |    +-------+     +----------------+
    |                         | exact revision on order/movement/reservation
    |                         v
    |                    +------+     +-----------+
    |                    | boms |---->| bom_lines |
    |                    +------+     +-----------+
    |                       |               |
    |                       v               v
    |              production_orders   production_order_materials
    |
    +--> +---------------------+     +--------------------------+
    |    | inventory_movements |---->| inventory_movement_lines |
    |    +---------------------+     +--------------------------+
    |                                             ^
    |                                             | from / to
    +--> +---------------------+-------------------+
         | inventory_locations |
         +---------------------+
              | parent location (self-reference)
              +--> stock_reservations
```

`inventory_on_hand` calculates incoming, outgoing, and physical on-hand quantities
from movement lines. `inventory_availability` reuses that view and subtracts active
reservations. Both are views, not additional storage tables. A missing row means
zero stock. Filter eligible locations when
planning; quarantine, damaged, and transit stock cannot be reserved.

## Changes and assumptions relative to the supplied fields

- The canonical spelling is `business_partners`.
- The supplied `stock_reservations` definition duplicated material requirements.
  Reservations instead store organization, exact item revision, location, quantity,
  status, timestamps, and exactly one owner: `sales_order_line_id` or
  `production_order_material_id`. Production order and BOM line are reached
  through the material requirement. Reservation statuses are ACTIVE, CONSUMED,
  and RELEASED. Reservations must start ACTIVE; terminal reservations cannot be
  reactivated, and their allocation and creation timestamp cannot be overwritten.
- Added `organization_id` to child tables and composite foreign keys to prevent
  linking records across organizations.
- Added `bom_id` and `component_revision_id` to production material snapshots
  so composite foreign keys enforce the selected order's BOM and exact component.
- IDs use BIGINT identity columns. Creation timestamps default to now(); updated
  timestamps have update triggers. Quantities are positive fixed-precision decimals.
- Company-specific codes, SKUs, and order numbers are unique within their
  organization. Revision codes are unique per item, BOM versions per product
  revision, and line numbers per document.
- Status/type values are checked by the database. Workflow transition rules,
  such as when an entire order may be marked completed, belong in the future
  transactional service layer.
- Location types provide V1 stock segregation: WAREHOUSE, BIN, PRODUCTION,
  QUARANTINE, DAMAGED, TRANSIT. The parent FK prevents self-parenting but does not
  reject longer hierarchy cycles; hierarchy management must validate those.
- Active BOM recipes and material snapshots cannot be edited. Production order
  product, BOM, source demand, and quantity are immutable. Inventory history is
  append-only; corrections use additional movements. Draft BOMs remain editable.
- Sales line quantities cannot be reduced below ACTIVE plus CONSUMED allocations.
  Demand changes use the same transaction lock as reservations, so concurrent
  allocations cannot bypass this check. Released reservations do not count.
- RECEIPT and PRODUCTION_OUTPUT movements require only a destination; SHIPMENT
  and PRODUCTION_CONSUMPTION require only a source; TRANSFER requires both.
  ADJUSTMENT permits either direction under the existing positive quantity and
  distinct location constraints.

Migration `0004_inventory_ledger` additionally supports ADJUSTMENT_IN (destination
only) and ADJUSTMENT_OUT (source only), which the inventory API uses for corrections.
It preserves historical ADJUSTMENT records and adds optional organization-scoped
idempotency keys to movement headers for safe retries.

These additional protections are installed by migration `0002_invariants`.
The applied `0001_v1` migration is unchanged. The new migration changes validation
for subsequent writes without rewriting existing history. Historical movement
classifications are not retroactively corrected.

Migration `0003_product_definition` renames PRODUCT items to FINISHED_GOOD and
APPROVED BOMs to ACTIVE while preserving recipe IDs and references. Only finished
goods can own BOMs; recipes cannot contain their own product revision or duplicate
component revisions. New BOMs start DRAFT and become ACTIVE through the API's
explicit activation operation after components have been added.

## Transaction contract for the V1 flow

1. Confirm the sales order and its exact promised item revisions.
2. Read eligible available stock. Reserve available finished goods against the
   sales order line. Calculate the shortage after those allocations.
3. Select an ACTIVE BOM for precisely that product revision; if multiple
   versions are active, choose explicitly. There is no implicit latest-BOM rule.
4. Create a production order for the shortage, linked to the sales order line.
   In the same transaction, insert all material snapshots using:
   `round(production_quantity * bom_line.quantity / bom.output_quantity, 6)`.
   The database validates each inserted snapshot. The service must ensure every
   BOM line is included and handle quantities that round below 0.000001.
5. Reserve each requirement against its exact component revision and stock
   location. If availability is insufficient, report shortages; an all-or-nothing
   material allocation should roll back the entire reservation transaction.
6. To manufacture or ship, mark the relevant reservations CONSUMED and insert
   their corresponding outgoing movements in the **same transaction**. Record
   production output as incoming finished goods. Partial use requires releasing
   the original reservation and creating appropriately sized allocations.
7. Reserve newly manufactured finished goods for the source demand, then ship.
   Update document statuses atomically with the corresponding business events.

All stock writes use READ COMMITTED transactions and a per-organization
transaction advisory lock. This deliberately serializes stock writes within one
organization in V1. Checks reject over-allocation, consuming reserved stock,
and reservation quantities above their owner's demand. Application transactions
spanning organizations should lock them in ascending ID order or retry deadlocks.
A failed statement must be rolled back before the connection is reused.

Stock validation failures include a PostgreSQL error code and relevant record IDs,
locations, and quantities in the error detail. Migration logs identify each applied
revision; configuration errors identify missing settings without printing passwords.

The schema does not yet enforce that a CONSUMED reservation has a corresponding
movement, nor that a shipment is linked to its originating sales line:
`inventory_movements.reference` is only a human-readable reference. The service
must implement atomic posting; typed source links would be a future schema
extension. Do not use direct stock DML as a substitute for that service.

## V1 boundaries

The requested 14 tables provide revision-level production and inventory. They
do not yet implement lot/serial genealogy, physical panel units, inspections,
financial postings, full actor/reason audit history, or price history.
`tracking_type` declares intent only; it does not store or enforce actual lots
or serial numbers. These remain future extensions needed for the broader ERP
design principles. No background purchasing flow or automatic shortage planner
is implemented here.

The Compose test service runs the API health test, migration configuration tests,
and database behavior tests. Database tests apply the actual Alembic chain in
disposable PostgreSQL schemas. They cover invalid input, missing records, rollback
and retry, quantity boundaries, reservation transitions, movement direction,
database read-only permissions, upgrade preservation, and repeatable migrations.
Concurrency tests verify actual lock contention between independent connections
for both competing reservations and demand reductions. Tests do not erase or
modify application records in the public schema. Database tests are skipped when
DB_HOST is absent; the Compose service supplies the required connection settings.
The read-only role test covers PostgreSQL privileges, not future API/RBAC behavior.

## References

- [Official PostgreSQL Docker image](https://hub.docker.com/_/postgres)
- [PostgreSQL transaction locks](https://www.postgresql.org/docs/17/explicit-locking.html)
- [Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
