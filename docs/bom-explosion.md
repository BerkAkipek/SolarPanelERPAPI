# BOM explosion — step 6

BOM explosion calculates the exact component materials required to produce a given quantity of finished goods:

```text
required_quantity = round(production_quantity * line_quantity / bom_output_quantity, 6)
```

## Pure domain function

Implemented as a database-free pure function:

```python
explode_bom(bom, production_quantity) -> list[MaterialRequirement]
```

- **Zero side effects:** No database mutations, no inventory adjustments, no locks acquired.
- **Deterministic:** Pure input-output transformation.
- **Precision:** Uses exact `Decimal` arithmetic with standard half-up rounding to six decimal places (`0.000001`), matching the database numeric storage contract.

### Solar panel example (PV-550 * 70 modules)

Given an active recipe for 1 module (`bom_output_quantity = 1`):
- Solar cells: 144 EA
- Glass: 1 EA
- EVA film: 2 M²
- Junction boxes: 1 EA

Running `explode_bom(bom, 70)` produces:

| Component | Quantity per unit | Required quantity |
| --- | ---: | ---: |
| Solar cells | 144.000000 | 10,080.000000 |
| Glass | 1.000000 | 70.000000 |
| EVA film | 2.000000 | 140.000000 |
| Junction boxes | 1.000000 | 70.000000 |

## HTTP Endpoint

### GET /production/boms/{bom_id}/explode?quantity={qty}

Requires `X-Organization-ID`.

Example response:

```json
{
  "bom_id": 1,
  "production_quantity": "70.000000",
  "requirements": [
    {
      "bom_line_id": 1,
      "line_no": 1,
      "component_revision_id": 2,
      "quantity_per_unit": "144.000000",
      "required_quantity": "10080.000000",
      "sku": "CELL-M10",
      "name": "Solar cells",
      "base_uom": "EA"
    },
    {
      "bom_line_id": 2,
      "line_no": 2,
      "component_revision_id": 3,
      "quantity_per_unit": "1.000000",
      "required_quantity": "70.000000",
      "sku": "GLASS-32",
      "name": "Tempered solar glass",
      "base_uom": "EA"
    }
  ]
}
```
