# Stock availability and reservations API — step 3

This step answers: **What do I actually have available to promise?**

```text
Available = OnHand - ActiveReservations
```

While `on-hand` reports physical stock across all locations, `available` reflects stock that is uncommitted and eligible to promise for orders or manufacturing.

All endpoints require `X-Organization-ID` in the request header.

## Endpoints

| Method | Path | Status | Behavior |
| --- | --- | --- | --- |
| GET | `/inventory/items/{revision_id}/availability` | 200 | Returns total and per-location physical on-hand, reserved, and available stock. |
| POST | `/inventory/reservations` | 201 | Creates an `ACTIVE` reservation against a sales order line or production order material. |
| GET | `/inventory/reservations/{reservation_id}` | 200 | Reads reservation state, timestamps, allocation, and status. |
| DELETE | `/inventory/reservations/{reservation_id}` | 200 | Soft-releases the reservation (`ACTIVE -> RELEASED`), restoring availability. Idempotent. |
| POST | `/inventory/reservations/{reservation_id}/consume` | 200 | Marks reservation `CONSUMED` during manufacturing or fulfillment. |

---

### GET /inventory/items/{revision_id}/availability

Returns the exact revision's available-to-promise balance:

```json
{
  "organization_id": 1,
  "item_revision_id": 1,
  "item_id": 1,
  "sku": "PV-550",
  "revision_code": "REV-A",
  "base_uom": "EA",
  "on_hand": "100.000000",
  "reserved": "70.000000",
  "available": "30.000000",
  "locations": [
    {
      "location_id": 1,
      "code": "RAW-WH",
      "name": "Raw warehouse",
      "on_hand": "100.000000",
      "reserved": "70.000000",
      "available": "30.000000"
    }
  ]
}
```

---

### POST /inventory/reservations

Reserves stock for a customer sales order line or manufacturing component requirement:

```json
{
  "item_revision_id": 1,
  "location_id": 1,
  "quantity": "70.000000",
  "sales_order_line_id": 1
}
```

Rules and invariants:
1. **Traceability:** In accordance with Option A, reservations require an owner: either `sales_order_line_id` or `production_order_material_id`.
2. **Reservable locations:** Only active locations with type `WAREHOUSE`, `BIN`, or `PRODUCTION` are eligible. Quarantine, damaged, and transit locations are rejected (`422 location_not_reservable`).
3. **Sufficient availability:** Reservation quantity cannot exceed currently available stock at the specified location (`409 insufficient_stock`).
4. **Demand boundary:** Total active and consumed reservations cannot exceed document demand (`409 reservation_exceeds_demand`).
5. **Initial state:** Every reservation starts `ACTIVE`.

---

### DELETE /inventory/reservations/{reservation_id}

Physical deletion is strictly prevented to preserve audit history. Calling `DELETE` performs a **soft release**:

```text
ACTIVE -> RELEASED
```

- Status transitions to `RELEASED`.
- The reserved quantity is unblocked, restoring availability.
- **Idempotency:** Releasing an already `RELEASED` reservation is safe and returns the released reservation (`200`).
- **Terminal safety:** Attempting to release a `CONSUMED` reservation is rejected (`409 reservation_terminal`).

---

### Concurrency and oversell protection

All reservation operations acquire PostgreSQL transaction advisory lock `pg_advisory_xact_lock(organization_id)`. Concurrent reservation requests serialize under `READ COMMITTED` isolation, guaranteeing that competing requests cannot oversell available stock.
