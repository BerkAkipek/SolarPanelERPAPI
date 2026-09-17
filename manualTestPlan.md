# Manual Test Plan & Acceptance Test Guide

This document provides a comprehensive, step-by-step manual test plan for validating the **Solar Panel ERP API (v1.0.0)**. It serves quality assurance (QA) engineers, API testers, and developers verifying acceptance criteria without automated test harnesses.

---

## 1. Test Environment Setup & Prerequisites

### 1.1. Start the Application
Ensure the Docker Compose stack is healthy:
```bash
docker compose up -d --build
docker compose ps
```
The `solarpanelerp-api-1` container should be healthy on port `8000`, and `solarpanelerp-db-1` on port `5432`.

### 1.2. Testing Tools
Tests can be executed via:
- **Interactive Swagger UI**: Open [`swagger.html`](file:///C:/Users/BERK/workSpace01/solarPanelERPAPI/swagger.html) in any browser or visit `http://localhost:8000/docs`.
- **cURL**: Standard Linux / macOS / Git Bash shell.
- **PowerShell**: Windows PowerShell using `Invoke-RestMethod`.

### 1.3. Tenant Header Requirement
All business endpoints require the header:
```http
X-Organization-ID: 1
```
An omitted or non-positive header returns HTTP `422 Unprocessable Entity`. An unknown organization returns HTTP `404 Not Found`.

---

## 2. Test Suite 1: System Health & Tenant Isolation

### TC-1.1: System Health Check
- **Objective**: Verify that the API server is up and responding.
- **Request**:
  ```bash
  curl -s -X GET http://localhost:8000/health
  ```
- **Expected Status**: `200 OK`
- **Expected Response**:
  ```json
  {"status": "ok"}
  ```

### TC-1.2: Tenant Isolation — Missing Organization Header
- **Objective**: Verify that business endpoints reject requests missing `X-Organization-ID`.
- **Request**:
  ```bash
  curl -s -X GET http://localhost:8000/items/1
  ```
- **Expected Status**: `422 Unprocessable Entity`
- **Expected Response Detail**: Missing required header `x-organization-id`.

### TC-1.3: Tenant Isolation — Non-Existent Organization
- **Objective**: Verify that referencing an unknown organization ID returns a clean 404.
- **Request**:
  ```bash
  curl -s -X GET http://localhost:8000/items/1 \
    -H "X-Organization-ID: 999999"
  ```
- **Expected Status**: `404 Not Found`
- **Expected Response**:
  ```json
  {"detail": {"code": "organization_not_found", "message": "Organization 999999 was not found."}}
  ```

---

## 3. Test Suite 2: Product Catalog & BOM Definition

### TC-2.1: Create Finished Solar Panel Item
- **Objective**: Create a finished solar module catalog item.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/items \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "sku": "PV-550-TEST",
      "name": "550W Bifacial Solar Panel",
      "item_type": "FINISHED_GOOD",
      "base_uom": "EA",
      "tracking_type": "SERIAL"
    }'
  ```
- **Expected Status**: `201 Created`
- **Verification**: Note the returned `id` (e.g., `101`). Status is active.

### TC-2.2: Duplicate SKU Rejection
- **Objective**: Verify that unique SKU constraint per organization is enforced.
- **Request**: Re-execute the request from TC-2.1 with identical payload.
- **Expected Status**: `409 Conflict`
- **Expected Response**:
  ```json
  {"detail": {"code": "duplicate_sku", "message": "SKU already exists in this organization."}}
  ```

### TC-2.3: Create Finished Item Revision
- **Objective**: Create `REV-A` revision for the panel item.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/items/101/revisions \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "revision_code": "REV-A",
      "status": "ACTIVE"
    }'
  ```
- **Expected Status**: `201 Created`
- **Verification**: Note `id` (e.g., `201`). Status is `ACTIVE`.

### TC-2.4: Create Raw Material Components & Revisions
- **Objective**: Create 5 component items and their active revisions.
- **Request 1 (Solar Cells)**:
  ```bash
  curl -s -X POST http://localhost:8000/items \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"sku": "CELL-TEST", "name": "M10 Monocrystalline Cell", "item_type": "COMPONENT", "base_uom": "EA"}'
  # Create revision REV-A for CELL-TEST (item_id: 102 -> rev_id: 202)
  curl -s -X POST http://localhost:8000/items/102/revisions \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"revision_code": "REV-A", "status": "ACTIVE"}'
  ```
- **Request 2 (Junction Box)**:
  ```bash
  curl -s -X POST http://localhost:8000/items \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"sku": "JB-TEST", "name": "1500V Split Junction Box", "item_type": "COMPONENT", "base_uom": "EA"}'
  # Create revision REV-A for JB-TEST (item_id: 103 -> rev_id: 203)
  curl -s -X POST http://localhost:8000/items/103/revisions \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"revision_code": "REV-A", "status": "ACTIVE"}'
  ```
- **Expected Status**: `201 Created` for all.

### TC-2.5: Create DRAFT Bill of Materials (BOM)
- **Objective**: Create a recipe header for the panel revision.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/boms \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "product_revision_id": 201,
      "version": 1,
      "output_quantity": "1.000000"
    }'
  ```
- **Expected Status**: `201 Created`
- **Expected Response**: `status: "DRAFT"`, `version: 1`, note `id` (e.g., `301`).

### TC-2.6: Negative Test — Prevent BOM for Non-Finished Goods
- **Objective**: Assert that a component item revision cannot have a BOM recipe.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/boms \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "product_revision_id": 202,
      "version": 1,
      "output_quantity": "1.000000"
    }'
  ```
- **Expected Status**: `422 Unprocessable Entity`
- **Expected Response**:
  ```json
  {"detail": {"code": "finished_good_required", "message": "Only FINISHED_GOOD items can have a BOM."}}
  ```

### TC-2.7: Add BOM Lines & Prevent Self-Reference
- **Objective**: Add component lines to draft BOM and assert self-reference prevention.
- **Test Self-Reference**:
  ```bash
  curl -s -X POST http://localhost:8000/boms/301/lines \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"line_no": 1, "component_revision_id": 201, "quantity": "1.000000"}'
  ```
  - **Expected Status**: `422 Unprocessable Entity` (`bom_self_reference`).
- **Add Valid Component Lines**:
  ```bash
  # Line 1: 144 Solar Cells
  curl -s -X POST http://localhost:8000/boms/301/lines \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"line_no": 1, "component_revision_id": 202, "quantity": "144.000000"}'
  # Line 2: 1 Junction Box
  curl -s -X POST http://localhost:8000/boms/301/lines \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"line_no": 2, "component_revision_id": 203, "quantity": "1.000000"}'
  ```
  - **Expected Status**: `201 Created` for each valid line.

### TC-2.8: Activate BOM Recipe & Enforce Immutability
- **Objective**: Lock the BOM recipe as `ACTIVE` and verify that further line additions are rejected.
- **Activate Recipe**:
  ```bash
  curl -s -X POST http://localhost:8000/boms/301/activate \
    -H "X-Organization-ID: 1"
  ```
  - **Expected Status**: `200 OK`, `status: "ACTIVE"`.
- **Verify Immutability**:
  ```bash
  curl -s -X POST http://localhost:8000/boms/301/lines \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{"line_no": 3, "component_revision_id": 202, "quantity": "10.000000"}'
  ```
  - **Expected Status**: `409 Conflict`
  - **Expected Response**:
    ```json
    {"detail": {"code": "bom_locked", "message": "This BOM is locked. Create a new version to change its recipe."}}
    ```

---

## 4. Test Suite 3: Double-Entry Inventory Ledger & Idempotency

### TC-3.1: Post Initial Component Receipts
- **Objective**: Post raw material receipts into Raw Warehouse (`RAW-WH`, location ID: `2`).
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/inventory/movements \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: REC-INIT-001" \
    -d '{
      "movement_type": "RECEIPT",
      "reference": "PO-SUPPLIER-001",
      "lines": [
        {"item_revision_id": 202, "to_location_id": 2, "quantity": "10000.000000"},
        {"item_revision_id": 203, "to_location_id": 2, "quantity": "50.000000"}
      ]
    }'
  ```
- **Expected Status**: `201 Created`
- **Verification**: Response contains movement `id` and lines.

### TC-3.2: Safe Idempotency Retry
- **Objective**: Re-send the identical receipt with the same `Idempotency-Key`.
- **Request**: Re-send identical payload with `Idempotency-Key: REC-INIT-001`.
- **Expected Status**: `201 Created` (returns original movement record without duplicating physical inventory).

### TC-3.3: Idempotency Conflict Detection
- **Objective**: Re-send the same `Idempotency-Key` with altered payload.
- **Request**: Re-send `Idempotency-Key: REC-INIT-001` with quantity `"20000.000000"`.
- **Expected Status**: `409 Conflict`
- **Expected Response**:
  ```json
  {"detail": {"code": "idempotency_conflict", "message": "This Idempotency-Key was already used for a different movement."}}
  ```

### TC-3.4: Receive Stock into Quarantine (QA-HOLD)
- **Objective**: Receive 20 Junction Boxes into Quarantine inspection bay (`QA-HOLD`, location ID: `3`).
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/inventory/movements \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "movement_type": "RECEIPT",
      "reference": "QA-INSPECTION-LOT-99",
      "lines": [
        {"item_revision_id": 203, "to_location_id": 3, "quantity": "20.000000"}
      ]
    }'
  ```
- **Expected Status**: `201 Created`

### TC-3.5: Negative Balance Prevention
- **Objective**: Attempt to issue more stock than physically available.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/inventory/movements \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "movement_type": "ADJUSTMENT_OUT",
      "lines": [
        {"item_revision_id": 203, "from_location_id": 2, "quantity": "9999.000000"}
      ]
    }'
  ```
- **Expected Status**: `409 Conflict`
- **Expected Response**:
  ```json
  {"detail": {"code": "insufficient_stock", "message": "Movement would consume more stock than available at the source location."}}
  ```

---

## 5. Test Suite 4: Stock Availability & Quarantine Isolation

### TC-4.1: Verify Physical On-Hand Stock vs Reservable Availability
- **Objective**: Assert that physical on-hand includes quarantine stock, while available stock strictly excludes it.
- **Step A: Check On-Hand Physical Balance**:
  ```bash
  curl -s -X GET http://localhost:8000/inventory/items/203/on-hand \
    -H "X-Organization-ID: 1"
  ```
  - **Expected**: Total on-hand = `70.000000` (50 in `RAW-WH` + 20 in `QA-HOLD`).
- **Step B: Check Available Stock**:
  ```bash
  curl -s -X GET http://localhost:8000/inventory/items/203/availability \
    -H "X-Organization-ID: 1"
  ```
  - **Expected**: Total available = `50.000000` (only `RAW-WH`). The 20 units in `QA-HOLD` are **strictly excluded**.

### TC-4.2: Prevent Stock Reservation in Quarantine
- **Objective**: Attempt to create a reservation directly in `QA-HOLD`.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/inventory/reservations \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "item_revision_id": 203,
      "location_id": 3,
      "quantity": "5.000000",
      "sales_order_line_id": 1
    }'
  ```
- **Expected Status**: `422 Unprocessable Entity`
- **Expected Response**:
  ```json
  {"detail": {"code": "location_not_reservable", "message": "Reservations are only permitted in active WAREHOUSE, BIN, or PRODUCTION locations."}}
  ```

---

## 6. Test Suite 5: Sales Orders & Fulfillment Analysis

### TC-5.1: Create Customer Sales Order
- **Objective**: Create sales order `SO-TEST-001` for 100 panels `PV-550`.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/sales-orders \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "order_number": "SO-TEST-001",
      "customer_id": 1,
      "currency_code": "USD",
      "lines": [
        {"line_no": 1, "item_revision_id": 201, "quantity": "100.000000", "unit_price": "185.5000"}
      ]
    }'
  ```
- **Expected Status**: `201 Created`, note `id` (e.g., `501`). Status is `DRAFT`.

### TC-5.2: Confirm Sales Order
- **Objective**: Confirm the sales order through explicit workflow.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/sales-orders/501/confirm \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`, `status: "CONFIRMED"`.

### TC-5.3: Execute Read-Only Fulfillment Analysis
- **Objective**: Verify calculation of `ship_from_stock` and `production_required`.
- **Precondition**: Assume 30 panels are on hand in `FG-WH`.
- **Request**:
  ```bash
  curl -s -X GET http://localhost:8000/sales-orders/501/fulfillment-analysis \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`
- **Expected Response**:
  ```json
  {
    "sales_order_id": 501,
    "order_number": "SO-TEST-001",
    "can_fulfill_entirely_from_stock": false,
    "total_ordered": "100.000000",
    "total_from_stock": "30.000000",
    "total_production_required": "70.000000",
    "lines": [
      {
        "line_no": 1,
        "sku": "PV-550-TEST",
        "ordered_quantity": "100.000000",
        "available_quantity": "30.000000",
        "ship_from_stock": "30.000000",
        "production_required": "70.000000"
      }
    ]
  }
  ```

---

## 7. Test Suite 6: BOM Explosion & Material Feasibility

### TC-7.1: Pure BOM Explosion
- **Objective**: Explode BOM recipe for missing 70 panels.
- **Request**:
  ```bash
  curl -s -X GET "http://localhost:8000/production/boms/301/explode?quantity=70" \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`
- **Expected Requirements**:
  - `CELL-TEST`: $70 \times 144 = 10,080.000000$ EA
  - `JB-TEST`: $70 \times 1 = 70.000000$ EA

### TC-7.2: Material Feasibility Analysis with Shortage Detection
- **Objective**: Verify that feasibility detects component shortage and ignores quarantine stock.
- **Request**:
  ```bash
  curl -s -X GET http://localhost:8000/sales-orders/501/material-feasibility \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`
- **Expected Response**:
  ```json
  {
    "sales_order_id": 501,
    "can_produce": false,
    "can_fulfill_all": false,
    "lines": [
      {
        "production_required": "70.000000",
        "can_produce": false,
        "materials": [
          {
            "sku": "CELL-TEST",
            "required": "10080.000000",
            "available": "10000.000000",
            "shortage": "80.000000"
          },
          {
            "sku": "JB-TEST",
            "required": "70.000000",
            "available": "50.000000",
            "shortage": "20.000000"
          }
        ]
      }
    ]
  }
  ```
- **Verification**: `JB-TEST` shortage is 20 because the 20 units in `QA-HOLD` are quarantined and non-reservable.

---

## 8. Test Suite 7: Atomic Production Execution & Loop Completion

### TC-8.1: Resolve Component Shortages via Goods Receipt
- **Objective**: Receive 1,000 Cells and 30 Junction Boxes into `RAW-WH`.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/inventory/movements \
    -H "X-Organization-ID: 1" -H "Content-Type: application/json" \
    -d '{
      "movement_type": "RECEIPT",
      "reference": "PO-REPLENISH-002",
      "lines": [
        {"item_revision_id": 202, "to_location_id": 2, "quantity": "1000.000000"},
        {"item_revision_id": 203, "to_location_id": 2, "quantity": "30.000000"}
      ]
    }'
  ```
- **Expected Status**: `201 Created`
- **Verification**: Available `CELL-TEST` is now 11,000; available `JB-TEST` is now 80. `can_produce` is now `true`.

### TC-8.2: Create Production Order in DRAFT Status
- **Objective**: Create manufacturing order tied to the sales order line.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/production-orders \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "production_order_number": "PO-TEST-001",
      "product_revision_id": 201,
      "source_sales_order_line_id": 1,
      "quantity": "70.000000"
    }'
  ```
- **Expected Status**: `201 Created`, note `id` (e.g., `601`). Status is `DRAFT`.
- **Verification**: Material snapshot requirements are frozen: 10,080 cells and 70 junction boxes. Zero reservations exist in `DRAFT`.

### TC-8.3: Release Production Order & Reserve Component Materials
- **Objective**: Transition order to `RELEASED` and place Option A reservations on raw materials.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/production-orders/601/release \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`, `status: "RELEASED"`.
- **Verification**: Active stock reservations link directly to `production_order_material_id`.

### TC-8.4: Start Production
- **Objective**: Transition manufacturing order to `IN_PROGRESS`.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/production-orders/601/start \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`, `status: "IN_PROGRESS"`.

### TC-8.5: Atomic Production Completion (Single Transaction)
- **Objective**: Atomically complete manufacturing, consuming reservations, issuing materials, and receiving finished panels.
- **Request**:
  ```bash
  curl -s -X POST http://localhost:8000/production-orders/601/complete \
    -H "X-Organization-ID: 1" \
    -H "Content-Type: application/json" \
    -d '{
      "to_location_id": 1
    }'
  ```
- **Expected Status**: `200 OK`, `status: "COMPLETED"`.
- **Verification of 4 Atomic Actions**:
  1. Material reservations transition to `CONSUMED`.
  2. Raw material issue movement (`PRODUCTION_CONSUMPTION`) posted for 10,080 cells and 70 junction boxes.
  3. Finished goods receipt movement (`PRODUCTION_OUTPUT`) posted for +70 panels into Finished Goods Warehouse (`location_id: 1`).
  4. Production order status is `COMPLETED`.

### TC-8.6: Re-verify Sales Order Fulfillment
- **Objective**: Verify that the sales order can now be fulfilled 100% from warehouse stock.
- **Request**:
  ```bash
  curl -s -X GET http://localhost:8000/sales-orders/501/fulfillment-analysis \
    -H "X-Organization-ID: 1"
  ```
- **Expected Status**: `200 OK`
- **Expected Response**:
  ```json
  {
    "sales_order_id": 501,
    "can_fulfill_entirely_from_stock": true,
    "total_ordered": "100.000000",
    "total_from_stock": "100.000000",
    "total_production_required": "0.000000"
  }
  ```
- **Verification**: The core V1 business loop is closed and successfully completed!
