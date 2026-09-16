# Fulfillment analysis API — step 5

Fulfillment analysis answers: **Can we ship this sales order immediately from stock, or must we manufacture additional panels?**

For each ordered line item, it calculates:
- `ordered_quantity` (`ordered`): Requested quantity on the sales line
- `available_quantity` (`available`): Uncommitted eligible finished goods stock across active reservable locations
- `ship_from_stock`: $\min(\text{ordered}, \text{available})$
- `production_required`: $\max(0, \text{ordered} - \text{ship\_from\_stock})$

Notice that:

$$\text{ship\_from\_stock} + \text{production\_required} = \text{ordered\_quantity}$$

## Core architectural principle: Separate decision from execution

Fulfillment analysis is **strictly read-only**:
- It does **not** create production orders.
- It does **not** reserve stock.
- It does **not** post movements or mutate inventory.
- It does **not** modify the sales order status.

Decision and execution remain decoupled.

## Endpoint

### GET /sales-orders/{order_id}/fulfillment-analysis

Header required: `X-Organization-ID`.

Example response:

```json
{
  "sales_order_id": 1,
  "order_number": "SO-2026-001",
  "status": "CONFIRMED",
  "can_fulfill_entirely_from_stock": false,
  "total_ordered": "100.000000",
  "total_from_stock": "30.000000",
  "total_production_required": "70.000000",
  "lines": [
    {
      "line_id": 1,
      "line_no": 1,
      "item_revision_id": 1,
      "sku": "PV-550",
      "revision_code": "REV-A",
      "ordered_quantity": "100.000000",
      "available_quantity": "30.000000",
      "ship_from_stock": "30.000000",
      "production_required": "70.000000",
      "ordered": "100.000000",
      "available": "30.000000"
    }
  ]
}
```

## Inventory eligibility rules

1. **Reservable locations only:** Only active locations of type `WAREHOUSE`, `BIN`, or `PRODUCTION` contribute to available stock. Quarantine, damaged, and transit stock is ignored.
2. **Multi-line allocation:** When multiple lines order the same item revision, unreserved stock is allocated in line order without double-counting.
