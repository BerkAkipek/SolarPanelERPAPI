# Material feasibility — step 7

Material feasibility answers the core ERP manufacturing question:

> **Can we manufacture the missing panels?**

It combines **BOM explosion** (Step 6) with **inventory availability** (Step 3) to evaluate whether physical component stocks are sufficient to fulfill required production quantities.

---

## Business formulas

For each required component $i$:

$$\text{required}_i = \sum_{\text{lines for } i} \text{exploded\_quantity}$$

$$\text{available}_i = \sum_{\text{reservable locations}} \max(0, \text{on\_hand} - \text{active\_reservations})$$

$$\text{shortage}_i = \max(0, \text{required}_i - \text{available}_i)$$

$$\text{can\_produce} = \forall i, \text{shortage}_i = 0$$

---

## Domain principles

1. **Strictly read-only:** Feasibility analysis does **not** create production orders, reserve stock, or adjust inventory balances. Decision and execution remain strictly decoupled.
2. **Pure domain function:** `evaluate_material_feasibility(requirements, available_quantities, production_required)` is pure, deterministic, and free of database side effects.
3. **Reservable locations only:** Only active locations of type `WAREHOUSE`, `BIN`, and `PRODUCTION` count towards available material stock. Stock in `QUARANTINE`, `DAMAGED`, or `TRANSIT` is excluded.
4. **Respects reservations:** Committed active reservations reduce available stock, ensuring feasibility does not promise already-reserved components.
5. **Component aggregation:** If a BOM contains multiple lines referencing the same component revision, requirements are aggregated prior to checking against the single available stock pool.

---

## HTTP Endpoints

### 1. Direct BOM Material Feasibility

```http
GET /production/boms/{bom_id}/feasibility?quantity={production_required}
```
*(Also supports `?production_required={qty}` as a parameter alias)*

#### Request headers:
- `X-Organization-ID: 1`

#### Example response (shortage detected):
```json
{
  "bom_id": 1,
  "production_required": 70,
  "can_produce": false,
  "materials": [
    {
      "component_revision_id": 2,
      "sku": "CELL-M10",
      "name": "Solar Cell M10",
      "base_uom": "EA",
      "required": 10080,
      "available": 12000,
      "shortage": 0
    },
    {
      "component_revision_id": 3,
      "sku": "JB-1500",
      "name": "Junction Box 1500V",
      "base_uom": "EA",
      "required": 70,
      "available": 50,
      "shortage": 20
    }
  ]
}
```

---

### 2. End-to-End Sales Order Feasibility

```http
GET /sales-orders/{order_id}/material-feasibility
```

Evaluates each line of a sales order:
- `ship_from_stock`: Panels fulfilled directly from finished goods inventory.
- `production_required`: Missing panels requiring manufacturing.
- Automatically resolves the `ACTIVE` BOM for missing panel revisions and evaluates component availability.

#### Example response:
```json
{
  "sales_order_id": 1,
  "order_number": "SO-2026-0001",
  "status": "CONFIRMED",
  "can_fulfill_all": false,
  "lines": [
    {
      "line_id": 1,
      "line_no": 1,
      "item_revision_id": 1,
      "sku": "PV-550",
      "revision_code": "REV-A",
      "ordered_quantity": 100,
      "ship_from_stock": 30,
      "production_required": 70,
      "bom_id": 1,
      "can_produce": false,
      "status_message": "Component shortages detected",
      "materials": [
        {
          "sku": "CELL-M10",
          "required": 10080,
          "available": 12000,
          "shortage": 0
        },
        {
          "sku": "JB-1500",
          "required": 70,
          "available": 50,
          "shortage": 20
        }
      ]
    }
  ]
}
```
