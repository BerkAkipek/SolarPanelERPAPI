# Product definition API — step 1

Run `docker compose up -d --build`. Swagger is at http://localhost:8000/docs;
the OpenAPI document is at http://localhost:8000/openapi.json.
Use `API_PORT` in `.env` to change the host port.

## Organization selection

Every product endpoint requires `X-Organization-ID`, a positive organization ID.
An unknown organization returns 404. Record lookups and references are scoped to
that organization. A record in another organization also returns 404.

This header selects an organization; it is **not authentication or authorization**.
The local API uses the existing development database owner. JWT/RBAC and a
restricted runtime database role have not been added in this step.

Organization administration has no API yet. If the database is empty, open
`docker compose exec db psql -U solar_erp -d solar_erp` and run:

```sql
INSERT INTO organizations (code, name)
VALUES ('SOLAR', 'Solar manufacturer')
ON CONFLICT (code) DO NOTHING;

SELECT id FROM organizations WHERE code = 'SOLAR';
```

Use the returned ID in the header. No example business records are seeded
automatically.

## Endpoints

| Method | Path | Behavior |
| --- | --- | --- |
| POST | /items | Create a material, component, or finished good; returns 201. |
| POST | /items/{item_id}/revisions | Create an exact revision; returns 201. |
| POST | /boms | Create a DRAFT recipe for a finished-good revision; returns 201. |
| POST | /boms/{bom_id}/lines | Add an exact component revision and quantity; returns 201. |
| POST | /boms/{bom_id}/activate | Lock a nonempty recipe as ACTIVE; returns 200. Repeating activation is safe. |
| GET | /items/{item_id} | Read an item. |
| GET | /items/{item_id}/revisions | Read revisions with their BOM summaries and IDs. |
| GET | /boms/{bom_id} | Read the product revision and ordered component recipe. |

Creation schemas reject unexpected fields, including organization overrides.
BOM status is controlled through activation, not an arbitrary request field.
Items accept `MATERIAL`, `COMPONENT`, and `FINISHED_GOOD`.
Revisions default to DRAFT; revision status is independent of BOM status.
BOMs default to version 1 and output quantity 1.

## Define PV-550 REV-A

PowerShell example; replace `1` with the organization ID obtained above.
Run once with these example SKUs. A repeated create returns a conflict instead
of creating a duplicate.

```powershell
$apiUrl = 'http://localhost:8000'
$headers = @{ 'X-Organization-ID' = '1' }

$panel = Invoke-RestMethod -Method Post -Uri "$apiUrl/items" -Headers $headers -ContentType 'application/json' -Body '{"sku":"PV-550","name":"550 W photovoltaic panel","item_type":"FINISHED_GOOD","base_uom":"EA"}'
$panelRevision = Invoke-RestMethod -Method Post -Uri "$apiUrl/items/$($panel.id)/revisions" -Headers $headers -ContentType 'application/json' -Body '{"revision_code":"REV-A","status":"ACTIVE"}'

$cell = Invoke-RestMethod -Method Post -Uri "$apiUrl/items" -Headers $headers -ContentType 'application/json' -Body '{"sku":"CELL-A","name":"Solar cell","item_type":"COMPONENT","base_uom":"EA"}'
$cellRevision = Invoke-RestMethod -Method Post -Uri "$apiUrl/items/$($cell.id)/revisions" -Headers $headers -ContentType 'application/json' -Body '{"revision_code":"REV-A","status":"ACTIVE"}'

$bomBody = @{ product_revision_id = $panelRevision.id; version = 1; output_quantity = '1' } | ConvertTo-Json
$bom = Invoke-RestMethod -Method Post -Uri "$apiUrl/boms" -Headers $headers -ContentType 'application/json' -Body $bomBody

$lineBody = @{ line_no = 1; component_revision_id = $cellRevision.id; quantity = '144' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$apiUrl/boms/$($bom.id)/lines" -Headers $headers -ContentType 'application/json' -Body $lineBody

Invoke-RestMethod -Method Post -Uri "$apiUrl/boms/$($bom.id)/activate" -Headers $headers
Invoke-RestMethod -Uri "$apiUrl/boms/$($bom.id)" -Headers $headers
```

The final response includes:

```json
{
  "product": {
    "sku": "PV-550",
    "revision_code": "REV-A",
    "base_uom": "EA"
  },
  "status": "ACTIVE",
  "output_quantity": "1.000000",
  "lines": [
    {
      "line_no": 1,
      "quantity": "144.000000",
      "component": {
        "sku": "CELL-A",
        "revision_code": "REV-A",
        "base_uom": "EA"
      }
    }
  ]
}
```

This is an abbreviated response and an illustrative recipe, not an engineering
specification. Full responses include IDs, organization, names, version, and
creation time. Quantities serialize as decimal strings to preserve precision.
Line quantities apply to the BOM's `output_quantity`; multi-level explosion,
material feasibility, and production execution are later steps. Physical posting
and balances are now covered by the [inventory ledger API](inventory-ledger.md).

## Recipe rules and errors

- Only a FINISHED_GOOD can own a BOM, and its exact product revision is mandatory.
- Output and line quantities must be positive, fit NUMERIC(18,6), and have at most
  six fractional digits. Excess precision is rejected rather than rounded.
- Line numbers are unique within a BOM. A component revision may occur only once;
  combine its quantity on one line.
- A BOM cannot use its own exact product revision as a component.
- ACTIVE and OBSOLETE BOMs are locked. Add a new version to change a locked recipe.
- Activation requires at least one component. It locks the same parent row as line
  insertion, so concurrent activation cannot permit a late edit.
- Direct database writes are also protected by constraints and triggers.

Missing records return 404, duplicate records and locked recipes return 409, and
invalid inputs or recipe rules return 422. Business errors have this shape:

```json
{"detail":{"code":"bom_locked","message":"This BOM is locked. Create a new version to change its recipe."}}
```

Pydantic input errors use FastAPI's standard `detail` list with field locations.
Every request runs in a transaction committed before the response is sent.
Failed writes roll back. Creation uniqueness guards against duplicate retries;
activation is idempotent. General idempotency keys are not implemented.

## Implementation and verification

- `app/products/router.py`: HTTP routes.
- `app/dependencies.py`: shared organization selection and request transaction scope.
- `app/products/schemas.py`: request validation and response shapes.
- `app/products/service.py`: scoped queries, creation, and activation.
- `app/products/models.py`: SQLAlchemy mappings; Alembic remains the schema owner.
- `app/config.py` and `app/db.py`: shared configuration and request transactions.
- `0003_product_definition`: migrates PRODUCT → FINISHED_GOOD and APPROVED → ACTIVE,
  preserves IDs/recipes/references, and adds BOM invariants. It fails on incompatible
  historical recipes instead of silently changing them.

Run `docker compose --profile test run --rm --build test`.
Tests use disposable PostgreSQL schemas, exercise the HTTP endpoints, verify
database constraints and cross-organization references, and prove lock contention
during concurrent activation. Existing ledger and migration tests run as well.

Implementation references:
[SQLAlchemy sessions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html),
[FastAPI dependency cleanup](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/),
[Pydantic field types](https://docs.pydantic.dev/latest/api/standard_library_types/).
