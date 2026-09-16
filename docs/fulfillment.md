# V1 Fulfillment Flow

## The Core Question Answered

Version 1 answers:
> **"Given a sales order, can we fulfill it from stock, do we need production, and if production is needed, do we have the required materials?"**

---

## 1. Sales Order Intake & Explicit Lifecycle

Sales orders capture customer demand for finished solar modules:
```
DRAFT ──(POST /sales-orders/{id}/confirm)──> CONFIRMED ──(POST /sales-orders/{id}/cancel)──> CANCELLED
```
- Orders start in `DRAFT` where lines can be added or adjusted.
- Confirming an order (`POST /sales-orders/{id}/confirm`) is an **explicit business operation**, not an arbitrary PATCH.
- Orders must contain at least one line and reference `ACTIVE` revisions to be confirmed.
- Cancelled orders can never be confirmed; confirmed orders with active reservations cannot be cancelled without releasing reservations first.

---

## 2. Read-Only Fulfillment Analysis

`GET /sales-orders/{id}/fulfillment-analysis` evaluates order fulfillment deterministically without mutating state:

- **Available Stock**: Measured exclusively from reservable locations (`WAREHOUSE`, `BIN`, `PRODUCTION`). Quarantine (`QUARANTINE`) and damaged locations are excluded.
- **Ship From Stock**: $\min(\text{ordered\_quantity}, \text{available\_stock})$.
- **Production Required**: $\max(0, \text{ordered\_quantity} - \text{ship\_from\_stock})$.
- **`can_fulfill_entirely_from_stock`**: `true` if every order line can be satisfied entirely from on-hand stock without manufacturing.

---

## 3. Pure BOM Explosion

For lines where $\text{production\_required} > 0$, the active BOM recipe is exploded:
$$\text{required\_quantity} = \text{round}\left(\text{production\_required} \times \frac{\text{line\_quantity}}{\text{bom\_output\_quantity}}, 6\right)$$

- Pure domain arithmetic implemented in `app/production/domain.py`.
- Zero database mutations during analysis.
- Fractional and non-standard output quantities supported cleanly.

---

## 4. Material Feasibility Analysis

`GET /sales-orders/{id}/material-feasibility` evaluates component feasibility:
$$\text{shortage} = \max(0, \text{required} - \text{available})$$
$$\text{can\_produce} = \text{all}(\text{shortage} = 0)$$

If all required components (cells, glass, EVA films, junction boxes, frames) have $\text{shortage} = 0$, manufacturing can begin immediately.

---

## 5. Production Order Creation & Execution

When manufacturing is required:
1. **Creation**: `POST /production-orders` creates order in `DRAFT` snapshotting product revision, active BOM version, quantity, and frozen material requirements.
2. **Release**: `POST /production-orders/{id}/release` verifies physical material availability and creates component reservations with Option A ownership (`production_order_material_id`). Insufficient materials prevent release with a `409 Conflict (insufficient_stock)`.
3. **Execution**: `POST /production-orders/{id}/start` moves order to `IN_PROGRESS`.
4. **Completion**: `POST /production-orders/{id}/complete` runs in **one atomic transaction**:
   - Consumes material reservations (`ACTIVE` ➔ `CONSUMED`).
   - Emits raw component consumption movements (`PRODUCTION_CONSUMPTION`).
   - Emits finished module output movement (`PRODUCTION_OUTPUT`).
   - Marks production order `COMPLETED`.

---

## 6. Closing the Loop

After production completion, re-analyzing fulfillment (`GET /sales-orders/{id}/fulfillment-analysis`) reflects the manufactured panels in stock:
- `production_required` drops to 0.
- `can_fulfill_entirely_from_stock` becomes `true`.
- The customer order can be reserved and staged for delivery.
