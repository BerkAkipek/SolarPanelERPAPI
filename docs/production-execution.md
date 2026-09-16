# Production execution — step 9

Production execution completes the physical loop in the solar panel ERP system.

> **RELEASED / IN_PROGRESS ──(POST /complete)──> COMPLETED**

---

## The atomic transaction

When completing a production order, the database executes four interrelated actions atomically in **one single transaction**:

1. **Consume material reservations:**
   - Active reservations tied to `production_order_material_id` transition from `ACTIVE` to `CONSUMED`.
2. **Post raw material consumption movement:**
   - An `inventory_movements` record of type `PRODUCTION_CONSUMPTION` is created.
   - For each component requirement, an `inventory_movement_lines` row issues the raw materials out of the source location (`from_location_id`).
   - The PostgreSQL trigger `validate_movement_balance` verifies that available physical balances do not drop below zero.
3. **Post finished-good output movement:**
   - An `inventory_movements` record of type `PRODUCTION_OUTPUT` is created.
   - An `inventory_movement_lines` row receipts the manufactured finished panels into the destination location (`to_location_id`, e.g. Finished Goods Warehouse).
4. **Mark production order COMPLETED:**
   - `production_orders.status` transitions to `COMPLETED`.

---

## Invariant: All-or-nothing rollback

If any check fails (e.g. missing reservation, insufficient physical balance, invalid or non-reservable location), **PostgreSQL rolls back the entire transaction**:
- Zero movements are recorded.
- Zero reservations are consumed.
- Physical balances and on-hand stocks remain unchanged.
- Production order remains in its previous status.

---

## HTTP Endpoints

### 1. Start Production

```http
POST /production-orders/{id}/start
X-Organization-ID: 1
```

Transitions order status from `RELEASED` to `IN_PROGRESS`.

---

### 2. Complete Production

```http
POST /production-orders/{id}/complete
X-Organization-ID: 1
Content-Type: application/json

{
  "to_location_id": 2
}
```

#### Example Response (Status: COMPLETED):
```json
{
  "id": 1,
  "production_order_number": "PO-00001",
  "product_revision_id": 1,
  "sku": "PV-550",
  "quantity": "100.000000",
  "status": "COMPLETED",
  "materials": [
    {
      "id": 1,
      "sku": "CELL-M10",
      "required_quantity": "14400.000000",
      "reserved_quantity": "0.000000",
      "consumed_quantity": "14400.000000"
    },
    {
      "id": 2,
      "sku": "JB-1500",
      "required_quantity": "100.000000",
      "reserved_quantity": "0.000000",
      "consumed_quantity": "100.000000"
    }
  ]
}
```
