# Inventory ledger API — step 2

The ledger answers what is physically on hand using:

```text
OnHand = Incoming - Outgoing
```

Run `docker compose up -d --build api`. The API and examples are available at
http://localhost:8000/docs. Both endpoints require `X-Organization-ID`, as in
[product definition](product-definition.md). That header selects the organization;
it is not authentication.

## Endpoints

### POST /inventory/movements

Creates the header and every line in one transaction, returning 201 and the
persisted movement with line IDs. A failure rolls back the entire movement.

```json
{
  "movement_type": "RECEIPT",
  "reference": "INITIAL-CELLS-001",
  "lines": [
    {
      "item_revision_id": 1,
      "to_location_id": 1,
      "quantity": "10000"
    }
  ]
}
```

The IDs in these examples must be replaced with your actual exact item revision
and location IDs. Quantities are in the item's base unit of measure.

| Type | from_location_id | to_location_id | Effect |
| --- | --- | --- | --- |
| RECEIPT | null/omitted | required | Incoming stock from outside the ledger. |
| TRANSFER | required | required, different from source | Outgoing at source, incoming at destination. |
| ADJUSTMENT_IN | null/omitted | required | Positive stock correction. |
| ADJUSTMENT_OUT | required | null/omitted | Negative stock correction. |

All quantities are **positive** decimals with at most six fractional digits and
at most twelve integer digits. Direction determines the sign; negative quantities,
zero, nonfinite values, and excess precision are rejected. At least one line is
required. All locations and revisions must belong to the selected organization.

`occurred_at` is optional and defaults to the database transaction time.
An explicit value must include a timezone; responses normalize timestamps to UTC.
On-hand includes all posted entries, regardless of their event timestamps. This
step does not implement historical/as-of balances or unit conversions.

A transfer of 500 cells from RAW-WH to PRODUCTION uses one line:

```json
{
  "movement_type": "TRANSFER",
  "reference": "MOVE-CELLS-001",
  "lines": [
    {
      "item_revision_id": 1,
      "from_location_id": 1,
      "to_location_id": 2,
      "quantity": "500"
    }
  ]
}
```

### GET /inventory/items/{revision_id}/on-hand

Returns the exact revision's physical incoming, outgoing, and on-hand quantities
per location and in total. It does not subtract reservations.

After the example receipt and transfer, the abbreviated response is:

```json
{
  "item_revision_id": 1,
  "sku": "CELL-A",
  "revision_code": "REV-A",
  "base_uom": "EA",
  "incoming_quantity": "10500.000000",
  "outgoing_quantity": "500.000000",
  "on_hand_quantity": "10000.000000",
  "locations": [
    {
      "location_id": 1,
      "code": "RAW-WH",
      "incoming_quantity": "10000.000000",
      "outgoing_quantity": "500.000000",
      "on_hand_quantity": "9500.000000"
    },
    {
      "location_id": 2,
      "code": "PRODUCTION",
      "incoming_quantity": "500.000000",
      "outgoing_quantity": "0",
      "on_hand_quantity": "500.000000"
    }
  ]
}
```

Transfers appear on both sides of the total calculation and cancel out. Decimal
strings preserve precision; clients should treat different trailing-zero formats
as equal numeric values.

An existing revision with no movements returns zero totals and an empty locations
list. Locations with prior movements remain visible even if their balance is zero.
An unknown or other-organization revision returns 404. All ledger locations count,
including quarantine, damaged, and transit locations; this is physical stock,
not a measure of stock eligible for allocation.

## Atomicity, insufficient stock, and retries

The existing rule forbids an outgoing line from consuming more stock than is
available at its source location for that exact revision. With no reservations,
this equals physical on-hand. Existing database safeguards for pre-existing
reservations remain in force; these endpoints do not create or change reservations.

Lines post in request order. For example, two outgoing lines of 40 and 70 from a
location holding 100 fail as a whole: the header and the first 40-unit line are
rolled back. A later line cannot supply stock to justify an earlier outgoing line.
A per-organization transaction lock prevents concurrent requests from both
spending the same stock.

Use an optional `Idempotency-Key` header for safe network retries:

```text
X-Organization-ID: 1
Idempotency-Key: initial-cells-001
```

- The same key and equivalent request return the original movement with status 201.
- The same key with different content returns 409 `idempotency_conflict`.
- Keys are scoped to the organization. A rolled-back request does not consume its key.
- Equivalent decimals and timezone representations count as the same content.
- Without a key, each successful POST intentionally creates a new movement.
- The human-readable `reference` is not a uniqueness or retry key.

Errors: 404 for missing/cross-organization references, 422 for invalid quantities
or direction, and 409 `insufficient_stock` for unavailable source stock. Failed
transactions leave the ledger unchanged and can be retried after correction.

Posted ledger rows cannot be updated or deleted. Correct mistakes with new
opposite-direction postings. This step does not add reservation endpoints,
available-to-promise calculations, sales fulfillment, or production execution.

## Set up locations for the example

Location administration is not yet exposed as an API. After creating organization
SOLAR as described in the product guide, open:

```powershell
docker compose exec db psql -U solar_erp -d solar_erp
```

Then create the two example locations:

```sql
INSERT INTO inventory_locations (organization_id, code, name, location_type)
SELECT o.id, v.code, v.name, v.location_type
FROM organizations o
CROSS JOIN (VALUES
    ('RAW-WH', 'Raw material warehouse', 'WAREHOUSE'),
    ('PRODUCTION', 'Production floor', 'PRODUCTION')
) AS v(code, name, location_type)
WHERE o.code = 'SOLAR'
ON CONFLICT (organization_id, code) DO NOTHING;

SELECT l.id, l.code, l.organization_id
FROM inventory_locations l JOIN organizations o ON o.id = l.organization_id
WHERE o.code = 'SOLAR';
```

Use those returned IDs and the revision ID returned by the product API. No example
stock is inserted automatically.

## Implementation and tests

- `app/inventory/` owns ledger requests, responses, posting, and on-hand queries.
- `app/models.py`, `app/schemas.py`, and `app/dependencies.py` share organization,
  identifier/decimal validation, and transaction scope with product definition.
- Migration `0004_inventory_ledger` adds explicit adjustment types, optional retry
  metadata, and the `inventory_on_hand` view. Existing movements are preserved.
- The older `inventory_availability` view now reuses `inventory_on_hand`, so the
  physical balance formula has one database definition. The API reads on-hand only.
- Legacy database ADJUSTMENT rows remain valid; new API postings must specify
  ADJUSTMENT_IN or ADJUSTMENT_OUT.

Run all tests:

```powershell
docker compose --profile test run --rm --build test
```

Tests cover receipts, transfers, adjustments, exact depletion, decimal arithmetic,
invalid input, organization isolation, full rollback after a failing second line,
sequential and simultaneous retries, and concurrent outgoing stock protection.
Test data lives in disposable schemas, leaving application inventory unchanged.
