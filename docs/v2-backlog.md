# V2 Roadmap & Backlog (V1 Freeze)

## Version 1.0.0 Status: FROZEN

As of **v1.0.0**, the core question of the Solar Panel ERP API is definitively answered:
> **"Given a sales order, can we fulfill it from stock, do we need production, and if production is needed, do we have the required materials?"**

The scope for Version 1 is strictly frozen. No scope creep (such as purchasing orders, customer invoicing, or factory floor routing) will enter V1. Maintenance on V1 is limited to bug fixes and performance tuning.

---

## V2 Feature Roadmap & Backlog

The following capabilities are scheduled for development in Version 2 and subsequent major releases:

### 1. Procurement & Purchasing (PO Management)
- **Purchase Orders (POs)**: Supplier contracts, purchase orders, expected delivery dates, and payment terms.
- **Automated Replenishment**: Generate draft purchase orders directly from material feasibility shortages (`GET /sales-orders/{id}/material-feasibility`).
- **Goods Receipt Notes (GRN)**: Formal dock receiving with supplier packing slip verification and lot tracking before warehouse release.
- **Supplier Price History**: Vendor catalogs, volume discount tiers, and currency fluctuations.

### 2. Physical Unit Genealogy (MES Level)
- **`PanelUnit` Separation**: Distinct identity for each physical manufactured solar panel module (`PanelUnit`) separate from the catalog definition (`PanelModel`).
- **Serial Number & Barcode Tracking**:
  - Serialization of panels at cell layup / framing.
  - Full backward traceability: defective EVA lot or junction box batch traced to all serialized panels.
  - Forward traceability: panel serial number linked to pallet, shipping container, and customer project.

### 3. Work Centers & Manufacturing Routing
- **Work Centers**: Stringing machines, layup stations, laminators, framing presses, potting stations, flash testers, and packaging stations.
- **Manufacturing Routing**: Step-by-step operational workflows with setup times, cycle times, and labor assignment.
- **Work-in-Progress (WIP) Tracking**: Real-time tracking of panels currently in lamination or curing.

### 4. Quality Management & Non-Conformance (NCR)
- **Quality Gates**:
  - Pre-lamination visual inspection.
  - Post-lamination Electroluminescence (EL) test for microcracks and inactive cell areas.
  - Flash simulator (IV curve test) measuring $P_{max}$, $V_{oc}$, $I_{sc}$, and fill factor.
- **Power Binning**: Classifying panels by flash test wattage into tolerance bins (e.g., 545W, 550W, 555W).
- **Non-Conformance Reports (NCR)**: Quarantine workflows, rework orders, and scrap authorization.

### 5. Financial Ledger & Cost Accounting
- **Inventory Valuation**: Moving average, FIFO, and standard cost tracking per component and module.
- **Production Cost Rollup**: Real-time material consumption cost + machine overhead + labor hours.
- **Invoicing & General Ledger**: Customer sales invoices (AR), supplier bills (AP), and double-entry general ledger journal entries generated automatically from physical inventory movements.

### 6. Solar Plant EPC Projects & Maintenance
- **Project Tracking**: Utility-scale and commercial rooftop EPC project tracking separate from factory manufacturing runs.
- **Site Layout & Inverter Stringing**: Mapping serialized panels to physical array tables, strings, and inverters.
- **Warranty & Lifecycle**: Warranty registration, field degradation monitoring, thermographic defect RMA claims, and end-of-life recycling.
