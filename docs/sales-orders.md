# Sales orders API — step 4

This step introduces **customer demand**: promising exact item revisions with quantities and prices.

All endpoints require `X-Organization-ID` in the request header.

## Explicit workflow

State transitions happen only through dedicated business operations, never via arbitrary PATCH updates:

```text
       create
         │
         ▼
      [DRAFT] ──confirm──▶ [CONFIRMED]
         │                     │
       cancel                cancel
         │                     │
         ▼                     ▼
    [CANCELLED]           [CANCELLED]
```

- **Confirmation rule:** Only `DRAFT` orders with at least one valid line can be confirmed.
- **Cancellation rule:** `CANCELLED` orders can never be confirmed. Orders with active or consumed stock allocations cannot be cancelled without releasing reservations first.
- **Idempotency:** Re-confirming a `CONFIRMED` order or re-cancelling a `CANCELLED` order returns `200` safely.

## Endpoints

| Method | Path | Status | Behavior |
| --- | --- | --- | --- |
| POST | `/sales-orders` | 201 | Creates a `DRAFT` sales order, optionally with lines. |
| GET | `/sales-orders/{order_id}` | 200 | Reads the sales order and its ordered lines. |
| POST | `/sales-orders/{order_id}/lines` | 201 | Appends an exact item revision line to a `DRAFT` order. |
| POST | `/sales-orders/{order_id}/confirm` | 200 | Explicitly confirms the order (`DRAFT -> CONFIRMED`). |
| POST | `/sales-orders/{order_id}/cancel` | 200 | Explicitly cancels the order (`DRAFT/CONFIRMED -> CANCELLED`). |

---

### POST /sales-orders

Creates a draft sales order:

```json
{
  "order_number": "SO-2026-001",
  "customer_id": 1,
  "currency_code": "EUR",
  "lines": [
    {
      "item_revision_id": 1,
      "quantity": "100.000000",
      "unit_price": "250.0000"
    }
  ]
}
```

Response:

```json
{
  "id": 1,
  "organization_id": 1,
  "order_number": "SO-2026-001",
  "customer_id": 1,
  "status": "DRAFT",
  "currency_code": "EUR",
  "ordered_at": "2026-09-16T13:30:00Z",
  "required_at": null,
  "created_at": "2026-09-16T13:30:00Z",
  "lines": [
    {
      "id": 1,
      "organization_id": 1,
      "sales_order_id": 1,
      "line_no": 1,
      "item_revision_id": 1,
      "sku": "PV-550",
      "revision_code": "REV-A",
      "quantity": "100.000000",
      "unit_price": "250.0000"
    }
  ]
}
```

---

### POST /sales-orders/{order_id}/lines

Adds a line to a `DRAFT` sales order:

```json
{
  "item_revision_id": 1,
  "quantity": "50.000000",
  "unit_price": "240.0000"
}
```

Attempting to add lines to a `CONFIRMED` or `CANCELLED` order returns `409 order_locked`.

---

### POST /sales-orders/{order_id}/confirm

Confirms customer demand:

- Validates that the order has at least one line (`422 empty_order`).
- Validates that all lines reference `ACTIVE` item revisions (`422 invalid_revision_status`).
- Rejects confirmation if the order was cancelled (`409 order_cancelled`).

---

### Invariants enforced

1. **Unique order number:** Unique within the organization (`409 duplicate_order_number`).
2. **Customer validation:** Customer must exist and be active (`404 customer_not_found` / `422 customer_inactive`).
3. **Revision validity:** Revisions must be `ACTIVE` and belong to the same organization. `DRAFT` or `OBSOLETE` revisions cannot be ordered.
4. **No arbitrary status mutation:** `PATCH /sales-orders/{id}` is not permitted (returns `405 Method Not Allowed`).
