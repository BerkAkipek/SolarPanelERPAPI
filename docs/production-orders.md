# Production order creation — step 8

Production order creation transforms demand or manufacturing plans into committed production runs.

Following **AGENTS.md** and the ERP principles:
1. **Decision and commitment are decoupled:** Production orders are created in `DRAFT` status without holding stock reservations.
2. **Immutable snapshots:** Product revision, BOM version, quantity, and exact material requirements are frozen into `production_order_materials` within one transaction. Database triggers prevent tampering with historical snapshots.
3. **Explicit workflow state machine:**
   ```text
   DRAFT ──(POST /release)──> RELEASED (materials reserved)
     │                           │
     └───(POST /cancel)──────────┴───> CANCELLED (reservations released)
   ```

---

## Transactional snapshot

When a production order is created:
1. Validates the active BOM for the product revision.
2. Explodes the BOM for the target quantity using pure domain arithmetic:
   $$\text{required\_quantity} = \operatorname{round}\left(\frac{\text{quantity} \times \text{bom\_line\_quantity}}{\text{bom\_output\_quantity}}, 6\right)$$
3. Inserts `production_orders` header in `DRAFT` status.
4. Inserts `production_order_materials` snapshot lines in the exact same transaction.
5. Zero reservations are created in `DRAFT` status.

---

## Explicit release & reservation

When an operator calls `POST /production-orders/{id}/release`:
1. Checks whether reservable stock is sufficient for every required component.
2. Creates active `StockReservation` rows linking directly to `production_order_material_id`.
3. If any component is short, the release request is rejected with `409 insufficient_stock`, leaving the order in `DRAFT`.
4. If all components are successfully reserved, the order status transitions to `RELEASED`.

---

## HTTP Endpoints

### 1. Create Production Order from Sales Order Line

```http
POST /production-orders
X-Organization-ID: 1
Content-Type: application/json

{
  "source_sales_order_line_id": 42,
  "quantity": "70"
}
```

*Or via the nested sales order route:*
```http
POST /sales-orders/1/production-orders
X-Organization-ID: 1
Content-Type: application/json

{
  "source_sales_order_line_id": 42,
  "quantity": "70"
}
```

#### Example Response (Status: DRAFT):
```json
{
  "id": 1,
  "organization_id": 1,
  "production_order_number": "PO-00001",
  "product_revision_id": 1,
  "sku": "PV-550",
  "revision_code": "REV-A",
  "bom_id": 1,
  "bom_version": 1,
  "source_sales_order_line_id": 42,
  "quantity": "70.000000",
  "status": "DRAFT",
  "materials": [
    {
      "id": 1,
      "bom_id": 1,
      "bom_line_id": 1,
      "component_revision_id": 2,
      "sku": "CELL-M10",
      "name": "Solar Cell M10",
      "base_uom": "EA",
      "required_quantity": "10080.000000",
      "reserved_quantity": "0.000000"
    },
    {
      "id": 2,
      "bom_id": 1,
      "bom_line_id": 2,
      "component_revision_id": 3,
      "sku": "JB-1500",
      "name": "Junction Box 1500V",
      "base_uom": "EA",
      "required_quantity": "70.000000",
      "reserved_quantity": "0.000000"
    }
  ]
}
```

---

### 2. Release Production Order (Reserve Materials)

```http
POST /production-orders/1/release
X-Organization-ID: 1
```

#### Example Response (Status: RELEASED):
```json
{
  "id": 1,
  "status": "RELEASED",
  "materials": [
    {
      "id": 1,
      "sku": "CELL-M10",
      "required_quantity": "10080.000000",
      "reserved_quantity": "10080.000000"
    },
    {
      "id": 2,
      "sku": "JB-1500",
      "required_quantity": "70.000000",
      "reserved_quantity": "70.000000"
    }
  ]
}
```

---

### 3. Cancel Production Order

```http
POST /production-orders/1/cancel
X-Organization-ID: 1
```

If the order was in `RELEASED` status, any active stock reservations are transitioned to `RELEASED`, returning the component stocks back to available inventory.
