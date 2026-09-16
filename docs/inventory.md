# Inventory Ledger & Availability

## Single Source of Truth for Physical Stock

The inventory ledger answers:
> **What physical inventory exists, in which state, and what is available to promise?**

In accordance with ERP Design Principle #7 (*"Inventory has state, not just quantity"*), inventory balances are never stored in a mutable counter column on products. Instead, balances are derived from an immutable, append-only double-entry ledger.

---

## Double-Entry Movement Model

Movements are posted via `POST /inventory/movements`:

| Movement Type | Source (`from_location_id`) | Destination (`to_location_id`) | Use Case |
| --- | --- | --- | --- |
| `RECEIPT` | `None` (External) | Required | Inbound supplier delivery |
| `SHIPMENT` | Required | `None` (External) | Outbound customer shipment |
| `TRANSFER` | Required | Required | Internal movement between warehouses/bays |
| `ADJUSTMENT_IN` | `None` | Required | Positive physical inventory count cycle |
| `ADJUSTMENT_OUT` | Required | `None` | Negative cycle count / scrap |
| `PRODUCTION_CONSUMPTION` | Required | `None` | Raw material consumption into manufacturing |
| `PRODUCTION_OUTPUT` | `None` | Required | Finished module output from production line |

### Invariants Protected by PostgreSQL Triggers
1. **Positive Quantities**: Quantities must be positive `NUMERIC(18,6)`. Direction (`from` vs `to`) supplies the sign.
2. **Never Negative Balances**: `validate_movement_balance()` database trigger ensures outgoing lines never drop a source location's physical on-hand balance below zero.
3. **Location Isolation**: Movements within an organization cannot reference locations belonging to another organization.
4. **Idempotency**: Requests with the same `Idempotency-Key` and identical payload return the existing movement. Differing payloads with the same key are rejected with `409 Conflict`.

---

## Physical On-Hand vs Available Stock

### 1. Physical On-Hand (`inventory_on_hand` View)
$$\text{OnHand}(\text{location}, \text{revision}) = \sum \text{Incoming} - \sum \text{Outgoing}$$
- Computed across all active and inactive locations.
- Accessible via `GET /inventory/items/{revision_id}/on-hand`.
- Physical truth: includes warehouse stock, production floor, and quarantine bays.

### 2. Available-to-Promise (`inventory_availability` View)
$$\text{Available} = \text{OnHand} - \text{ActiveReservations}$$
- Accessible via `GET /inventory/items/{revision_id}/availability`.
- **Reservable Locations Invariant**: Only active locations of type `WAREHOUSE`, `BIN`, and `PRODUCTION` count towards available stock and material feasibility.
- Quarantine (`QUARANTINE`), damaged (`DAMAGED`), and transit (`TRANSIT`) locations are physically tracked but excluded from available stock.

---

## Stock Reservations & Traceability (Option A)

Stock reservations temporarily lock available inventory to prevent double-promising:
- `POST /inventory/reservations`: Reserve specific revision and location.
- `POST /inventory/reservations/{id}/release`: Restores available stock.
- `POST /inventory/reservations/{id}/consume`: Marks stock consumed by production.
- Physical database rows are never deleted. Status transitions explicitly:
  $$\text{ACTIVE} \longrightarrow \text{RELEASED} \quad \text{or} \quad \text{ACTIVE} \longrightarrow \text{CONSUMED}$$

### Option A Ownership Invariant
In accordance with ERP Design Principle #4 (*"Lot + serial genealogy"*), reservations belong to either:
1. A sales order line (`sales_order_line_id`) for customer fulfillment, OR
2. A production order material line (`production_order_material_id`) for raw component allocation.
Arbitrary or untraced reservations are prevented by database foreign key constraints.
