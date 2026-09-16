# Domain model

All business records belong to an organization. The current API selects this tenant scope with X-Organization-ID.

| Entity | Meaning |
| --- | --- |
| Organization | ERP owner and tenant boundary |
| Business partner | Customer identity |
| Item | Stable catalog identity, SKU, type, UOM |
| Item revision | Exact version used by a transaction |
| BOM | Versioned recipe for one exact product revision |
| BOM line | Component revision and quantity per output |
| Inventory location | Physical or logical stock location |
| Inventory movement | Immutable physical event header |
| Movement line | Positive quantity entering or leaving a location |
| Sales order / line | Future customer demand and exact promised revision |
| Production order | Future manufacturing request using an exact BOM |
| Material requirement | Frozen BOM requirement for one production order |
| Stock reservation | Future allocation against a sales line or requirement |

Relationships: Organization owns items, revisions, locations, movements, orders, and partners. Item has revisions. A finished-good revision has BOMs; BOMs have component revision lines. Movements have lines that reference revisions and source/destination locations. Future sales lines can source production orders and reservations.

inventory_on_hand is the single physical balance calculation. inventory_availability subtracts active reservations. Catalog identity and physical ledger quantities are never silently duplicated.
