# Production & Execution

## Manufacturing in V1

The production module translates unfulfilled customer demand into physical finished solar modules.

---

## 1. Product Recipes & BOM Invariants

- A Bill of Materials (`BOM`) represents the engineering recipe to produce a specific finished good revision (e.g., `PV-550 REV-A`).
- Recipes are versioned (`version = 1, 2, ...`) and specify output quantity.
- **Explicit BOM Lifecycle**:
  $$\text{DRAFT} \overset{\text{add lines \& activate}}{\longrightarrow} \text{ACTIVE} \longrightarrow \text{OBSOLETE}$$
- Active and obsolete BOMs are strictly immutable. PostgreSQL trigger `protect_bom()` ensures components cannot repeat, self-reference the finished product revision, or be modified once activated.

---

## 2. Production Orders

A production order snapshots the demand and recipe at creation time:
- Header records: product revision, BOM ID, output quantity, optional source sales order line, and status.
- Material snapshot (`production_order_materials`): records exact component lines, required quantities, reserved quantities, and consumed quantities.
- **Snapshot Immutability**: If a newer BOM version is created later, existing production orders remain bound to the recipe they were created with.

---

## 3. The Production Lifecycle

```
DRAFT ──(POST /release)──> RELEASED ──(POST /start)──> IN_PROGRESS ──(POST /complete)──> COMPLETED
  │                           │                            │                               │
  │                           │                            └──(atomic rollback on error)───┘
  └───(POST /cancel)──────────┴───────────────────────────> CANCELLED
```

### Endpoints:
- `POST /production-orders`: Creates order in `DRAFT` with calculated material requirements.
- `POST /production-orders/{id}/release`: Verifies component stock availability and creates reservations (`Option A`). Rejects with `409 Conflict (insufficient_stock)` if components are lacking.
- `POST /production-orders/{id}/start`: Transitions `RELEASED` ➔ `IN_PROGRESS`.
- `POST /production-orders/{id}/complete`: Executes completion atomically.
- `POST /production-orders/{id}/cancel`: Allowed from `DRAFT`, `RELEASED`, or `IN_PROGRESS`. Reverts any active component reservations back to `RELEASED` status.

---

## 4. Atomic Execution Invariant

Production order completion (`complete_production_order`) is the critical transaction of the manufacturing flow:

Within **one single database transaction** protected by an advisory lock:
1. Active material reservations are transitioned to `CONSUMED`.
2. Raw component consumption movements (`PRODUCTION_CONSUMPTION`) are posted from source warehouses.
3. Finished module output movement (`PRODUCTION_OUTPUT`) is posted into the destination warehouse.
4. Production order status is marked `COMPLETED`.

### All-or-Nothing Guarantee:
If physical balances are insufficient or an invalid location is provided:
- The entire transaction rolls back.
- No partial movements leak.
- No reservations are consumed.
- Order status remains unchanged.
