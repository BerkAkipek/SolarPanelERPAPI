"""End-to-End V1 Integration Test for the Solar Panel ERP API.

Answers the core V1 business questions:
1. Given a sales order, can we fulfill it from stock?
2. Do we need production?
3. If production is needed, do we have the required materials?
4. Can we execute production atomically to satisfy the customer demand?
"""
from decimal import Decimal
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.inventory.models import InventoryMovement, InventoryMovementLine, StockReservation
from app.main import app
from app.production.models import ProductionOrder, ProductionOrderMaterial
from tests.db_support import connect, in_schema, migrate_schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"), reason="Use the Docker Compose test service.",
)


@pytest.fixture(scope="module")
def v1_e2e_schema():
    name = "test_v1_e2e_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            # Seed core organization, partner, items, revisions, locations, and BOM
            connection.execute("""
                INSERT INTO organizations(id, code, name) VALUES (1, 'SOLARIA', 'Solaria Dynamics Inc.');
                INSERT INTO business_partners(id, organization_id, code, name)
                VALUES (1, 1, 'HELIOS', 'Helios Energy Solutions');

                -- Finished good: 550W Solar Panel
                INSERT INTO items(id, organization_id, sku, name, item_type, base_uom)
                VALUES (1, 1, 'PV-550', '550W Half-Cell Monocrystalline Solar Panel', 'FINISHED_GOOD', 'EA'),
                       (2, 1, 'CELL-M10', '182mm Monocrystalline Solar Cell', 'COMPONENT', 'EA'),
                       (3, 1, 'GLASS-3.2', '3.2mm Solar Tempered Glass Sheet', 'COMPONENT', 'EA'),
                       (4, 1, 'EVA-SHEET', 'Solar Encapsulant EVA Film Sheet', 'COMPONENT', 'EA'),
                       (5, 1, 'JB-1500', 'IP68 1500V Split Junction Box', 'COMPONENT', 'EA'),
                       (6, 1, 'FRAME-68', 'Anodized Aluminium Frame 35mm', 'COMPONENT', 'EA');

                -- Item revisions
                INSERT INTO item_revisions(id, organization_id, item_id, revision_code, status)
                VALUES (1, 1, 1, 'REV-A', 'ACTIVE'),
                       (2, 1, 2, 'REV-A', 'ACTIVE'),
                       (3, 1, 3, 'REV-A', 'ACTIVE'),
                       (4, 1, 4, 'REV-A', 'ACTIVE'),
                       (5, 1, 5, 'REV-A', 'ACTIVE'),
                       (6, 1, 6, 'REV-A', 'ACTIVE');

                -- Inventory locations: Warehouses (reservable) and Quarantine (non-reservable)
                INSERT INTO inventory_locations(id, organization_id, code, name, location_type)
                VALUES (1, 1, 'FG-WH', 'Finished Goods Warehouse', 'WAREHOUSE'),
                       (2, 1, 'RAW-WH', 'Raw Material Warehouse', 'WAREHOUSE'),
                       (3, 1, 'QA-HOLD', 'Quarantine Inspection Bay', 'QUARANTINE');

                -- Active BOM for PV-550 Rev A (recipe for 1 panel)
                -- 144 cells, 1 front glass, 2 EVA sheets, 1 junction box, 1 frame
                INSERT INTO boms(id, organization_id, product_revision_id, version, output_quantity)
                VALUES (1, 1, 1, 1, 1);
                INSERT INTO bom_lines(id, organization_id, bom_id, line_no, component_revision_id, quantity)
                VALUES (1, 1, 1, 1, 2, 144),
                       (2, 1, 1, 2, 3, 1),
                       (3, 1, 1, 3, 4, 2),
                       (4, 1, 1, 4, 5, 1),
                       (5, 1, 1, 5, 6, 1);
                UPDATE boms SET status = 'ACTIVE' WHERE id = 1;

                -- Initial Physical Inventory:
                -- 30 panels in FG-WH (customer needs 100, so 70 must be manufactured)
                -- 12,000 cells in RAW-WH (need 70 * 144 = 10,080 -> sufficient)
                -- 100 glass sheets in RAW-WH (need 70 -> sufficient)
                -- 200 EVA sheets in RAW-WH (need 140 -> sufficient)
                -- 50 junction boxes in RAW-WH (need 70 -> SHORTAGE OF 20!)
                -- 20 junction boxes in QA-HOLD (quarantine -> MUST NOT count towards availability!)
                -- 100 aluminium frames in RAW-WH (need 70 -> sufficient)
                INSERT INTO inventory_movements(organization_id, movement_type, reference)
                VALUES (1, 'RECEIPT', 'INITIAL-STOCK');

                INSERT INTO inventory_movement_lines(organization_id, movement_id, item_revision_id, to_location_id, quantity)
                VALUES (1, 1, 1, 1, 30),      -- 30 PV-550 in FG-WH
                       (1, 1, 2, 2, 12000),   -- 12000 cells in RAW-WH
                       (1, 1, 3, 2, 100),     -- 100 glass in RAW-WH
                       (1, 1, 4, 2, 200),     -- 200 EVA in RAW-WH
                       (1, 1, 5, 2, 50),      -- 50 JB in RAW-WH
                       (1, 1, 5, 3, 20),      -- 20 JB in QA-HOLD (quarantine)
                       (1, 1, 6, 2, 100);     -- 100 frame in RAW-WH

                SELECT setval(pg_get_serial_sequence('organizations', 'id'), coalesce(max(id), 1)) FROM organizations;
                SELECT setval(pg_get_serial_sequence('business_partners', 'id'), coalesce(max(id), 1)) FROM business_partners;
                SELECT setval(pg_get_serial_sequence('items', 'id'), coalesce(max(id), 1)) FROM items;
                SELECT setval(pg_get_serial_sequence('item_revisions', 'id'), coalesce(max(id), 1)) FROM item_revisions;
                SELECT setval(pg_get_serial_sequence('inventory_locations', 'id'), coalesce(max(id), 1)) FROM inventory_locations;
                SELECT setval(pg_get_serial_sequence('boms', 'id'), coalesce(max(id), 1)) FROM boms;
                SELECT setval(pg_get_serial_sequence('bom_lines', 'id'), coalesce(max(id), 1)) FROM bom_lines;
                SELECT setval(pg_get_serial_sequence('inventory_movements', 'id'), coalesce(max(id), 1)) FROM inventory_movements;
                SELECT setval(pg_get_serial_sequence('inventory_movement_lines', 'id'), coalesce(max(id), 1)) FROM inventory_movement_lines;
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture(scope="module")
def v1_e2e_engine(v1_e2e_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(v1_e2e_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def client(v1_e2e_engine):
    def test_session():
        with Session(v1_e2e_engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


def test_v1_complete_end_to_end_manufacturing_and_fulfillment_lifecycle(client, v1_e2e_engine):
    """Full V1 scenario:

    1. Place and confirm Sales Order for 100 PV-550 panels.
    2. Fulfillment analysis confirms: 30 from stock, 70 production required.
    3. Feasibility analysis detects shortage: 20 Junction Boxes missing (quarantine ignored).
    4. Attempting to release production order fails due to insufficient stock.
    5. Receive 30 missing junction boxes into raw material warehouse.
    6. Feasibility analysis confirms production is now feasible (can_produce: true).
    7. Production order is released, successfully reserving all raw components.
    8. Production order is started (IN_PROGRESS).
    9. Production order is completed atomically (reservations consumed, components deducted, 70 panels added to FG-WH).
    10. Re-checking fulfillment analysis proves 100 panels are now available in stock, 100% fulfillable from stock.
    """
    # -------------------------------------------------------------------------
    # Step 1: Customer Helios orders 100 panels of PV-550
    # -------------------------------------------------------------------------
    so_payload = {
        "order_number": "SO-2026-V1-001",
        "customer_id": 1,
        "currency_code": "USD",
        "lines": [
            {
                "line_no": 1,
                "item_revision_id": 1,  # PV-550 Rev A
                "quantity": "100",
                "unit_price": "180.00",
            }
        ],
    }
    so_resp = client.post("/sales-orders", json=so_payload)
    assert so_resp.status_code == 201, so_resp.json()
    order = so_resp.json()
    order_id = order["id"]
    order_line_id = order["lines"][0]["id"]
    assert order["status"] == "DRAFT"

    # Confirm the Sales Order
    confirm_resp = client.post(f"/sales-orders/{order_id}/confirm")
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "CONFIRMED"

    # -------------------------------------------------------------------------
    # Step 2: Fulfillment Analysis: Can we fulfill from stock? Do we need production?
    # -------------------------------------------------------------------------
    fa_resp = client.get(f"/sales-orders/{order_id}/fulfillment-analysis")
    assert fa_resp.status_code == 200
    fa_data = fa_resp.json()
    assert fa_data["can_fulfill_entirely_from_stock"] is False
    assert len(fa_data["lines"]) == 1

    line_fa = fa_data["lines"][0]
    assert line_fa["sku"] == "PV-550"
    assert Decimal(str(line_fa["ordered_quantity"])) == Decimal("100")
    assert Decimal(str(line_fa["available_quantity"])) == Decimal("30")
    assert Decimal(str(line_fa["ship_from_stock"])) == Decimal("30")
    assert Decimal(str(line_fa["production_required"])) == Decimal("70")

    # -------------------------------------------------------------------------
    # Step 3: Material Feasibility Analysis: Can we manufacture the 70 missing panels?
    # -------------------------------------------------------------------------
    mf_resp = client.get(f"/sales-orders/{order_id}/material-feasibility")
    assert mf_resp.status_code == 200
    mf_data = mf_resp.json()

    assert mf_data["can_fulfill_all"] is False
    assert len(mf_data["lines"]) == 1

    line_mf = mf_data["lines"][0]
    assert Decimal(str(line_mf["production_required"])) == Decimal("70")
    assert line_mf["can_produce"] is False

    # Check component requirements and shortages
    materials_by_sku = {m["sku"]: m for m in line_mf["materials"]}

    # CELL-M10: 70 * 144 = 10,080 required, 12,000 available -> shortage = 0
    assert Decimal(str(materials_by_sku["CELL-M10"]["required"])) == Decimal("10080")
    assert Decimal(str(materials_by_sku["CELL-M10"]["available"])) == Decimal("12000")
    assert Decimal(str(materials_by_sku["CELL-M10"]["shortage"])) == Decimal("0")

    # GLASS-3.2: 70 * 1 = 70 required, 100 available -> shortage = 0
    assert Decimal(str(materials_by_sku["GLASS-3.2"]["required"])) == Decimal("70")
    assert Decimal(str(materials_by_sku["GLASS-3.2"]["available"])) == Decimal("100")
    assert Decimal(str(materials_by_sku["GLASS-3.2"]["shortage"])) == Decimal("0")

    # EVA-SHEET: 70 * 2 = 140 required, 200 available -> shortage = 0
    assert Decimal(str(materials_by_sku["EVA-SHEET"]["required"])) == Decimal("140")
    assert Decimal(str(materials_by_sku["EVA-SHEET"]["available"])) == Decimal("200")
    assert Decimal(str(materials_by_sku["EVA-SHEET"]["shortage"])) == Decimal("0")

    # JB-1500: 70 * 1 = 70 required.
    # Note: RAW-WH has 50, QA-HOLD has 20. Only RAW-WH is reservable -> available = 50. Shortage = 20!
    assert Decimal(str(materials_by_sku["JB-1500"]["required"])) == Decimal("70")
    assert Decimal(str(materials_by_sku["JB-1500"]["available"])) == Decimal("50")
    assert Decimal(str(materials_by_sku["JB-1500"]["shortage"])) == Decimal("20")

    # FRAME-68: 70 * 1 = 70 required, 100 available -> shortage = 0
    assert Decimal(str(materials_by_sku["FRAME-68"]["required"])) == Decimal("70")
    assert Decimal(str(materials_by_sku["FRAME-68"]["available"])) == Decimal("100")
    assert Decimal(str(materials_by_sku["FRAME-68"]["shortage"])) == Decimal("0")

    # -------------------------------------------------------------------------
    # Step 4: Create Production Order in DRAFT
    # -------------------------------------------------------------------------
    po_payload = {
        "production_order_number": "PO-2026-V1-001",
        "product_revision_id": 1,
        "bom_id": 1,
        "quantity": "70",
        "source_sales_order_line_id": order_line_id,
    }
    po_resp = client.post("/production-orders", json=po_payload)
    assert po_resp.status_code == 201
    po = po_resp.json()
    po_id = po["id"]
    assert po["status"] == "DRAFT"
    assert len(po["materials"]) == 5

    # -------------------------------------------------------------------------
    # Step 5: Attempting to release must fail because JB-1500 has insufficient stock
    # -------------------------------------------------------------------------
    release_fail = client.post(f"/production-orders/{po_id}/release")
    assert release_fail.status_code == 409
    assert release_fail.json()["detail"]["code"] == "insufficient_stock"

    # Verify order is still in DRAFT
    po_check = client.get(f"/production-orders/{po_id}").json()
    assert po_check["status"] == "DRAFT"

    # -------------------------------------------------------------------------
    # Step 6: Receive Supplier Delivery of 30 Junction Boxes into RAW-WH
    # -------------------------------------------------------------------------
    receipt_payload = {
        "movement_type": "RECEIPT",
        "reference": "GRN-SUPPLIER-JB-001",
        "lines": [
            {
                "item_revision_id": 5,  # JB-1500
                "to_location_id": 2,    # RAW-WH
                "quantity": "30",
            }
        ],
    }
    rcpt_resp = client.post("/inventory/movements", json=receipt_payload)
    assert rcpt_resp.status_code == 201

    # -------------------------------------------------------------------------
    # Step 7: Re-evaluate Feasibility: can_produce is now TRUE!
    # -------------------------------------------------------------------------
    mf_resp2 = client.get(f"/sales-orders/{order_id}/material-feasibility")
    assert mf_resp2.status_code == 200
    mf_data2 = mf_resp2.json()
    assert mf_data2["can_fulfill_all"] is True
    assert mf_data2["lines"][0]["can_produce"] is True

    jb_material = [m for m in mf_data2["lines"][0]["materials"] if m["sku"] == "JB-1500"][0]
    assert Decimal(str(jb_material["available"])) == Decimal("80")  # 50 + 30
    assert Decimal(str(jb_material["shortage"])) == Decimal("0")

    # -------------------------------------------------------------------------
    # Step 8: Release Production Order -> Status RELEASED + Material Reservations
    # -------------------------------------------------------------------------
    release_resp = client.post(f"/production-orders/{po_id}/release")
    assert release_resp.status_code == 200
    po_released = release_resp.json()
    assert po_released["status"] == "RELEASED"

    # Verify reservations were created for all 5 materials
    for m in po_released["materials"]:
        assert Decimal(str(m["reserved_quantity"])) == Decimal(str(m["required_quantity"]))
        assert Decimal(str(m["consumed_quantity"])) == Decimal("0")

    # Verify inventory availability reflects reservations:
    # JB-1500: on_hand = 80, reserved = 70, available = 10
    with Session(v1_e2e_engine) as session:
        jb_avail = session.execute(text("""
            SELECT on_hand_quantity, reserved_quantity, available_quantity
            FROM inventory_availability
            WHERE item_revision_id = 5 AND location_id = 2
        """)).fetchone()
        assert jb_avail == (Decimal("80"), Decimal("70"), Decimal("10"))

    # -------------------------------------------------------------------------
    # Step 9: Start Production Execution -> Status IN_PROGRESS
    # -------------------------------------------------------------------------
    start_resp = client.post(f"/production-orders/{po_id}/start")
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "IN_PROGRESS"

    # -------------------------------------------------------------------------
    # Step 10: Complete Production Execution Atomically
    # -------------------------------------------------------------------------
    # Deposit completed panels into FG-WH (location 1)
    complete_resp = client.post(
        f"/production-orders/{po_id}/complete",
        json={"to_location_id": 1},
    )
    assert complete_resp.status_code == 200
    po_completed = complete_resp.json()
    assert po_completed["status"] == "COMPLETED"

    # Verify materials marked consumed and reservations zeroed
    for m in po_completed["materials"]:
        assert Decimal(str(m["consumed_quantity"])) == Decimal(str(m["required_quantity"]))
        assert Decimal(str(m["reserved_quantity"])) == Decimal("0")

    # -------------------------------------------------------------------------
    # Step 11: Verify Resulting Physical Stock and Balances
    # -------------------------------------------------------------------------
    with Session(v1_e2e_engine) as session:
        # PV-550 in FG-WH: Started with 30, produced 70 -> Total On Hand = 100!
        panel_stock = session.execute(text("""
            SELECT on_hand_quantity, reserved_quantity, available_quantity
            FROM inventory_availability
            WHERE item_revision_id = 1 AND location_id = 1
        """)).fetchone()
        assert panel_stock == (Decimal("100"), Decimal("0"), Decimal("100"))

        # Cells in RAW-WH: Started with 12000, consumed 10080 -> 1920 left on hand
        cell_stock = session.execute(text("""
            SELECT on_hand_quantity, reserved_quantity, available_quantity
            FROM inventory_availability
            WHERE item_revision_id = 2 AND location_id = 2
        """)).fetchone()
        assert cell_stock == (Decimal("1920"), Decimal("0"), Decimal("1920"))

        # JB-1500 in RAW-WH: Started with 50 + 30 = 80, consumed 70 -> 10 left on hand
        jb_stock = session.execute(text("""
            SELECT on_hand_quantity, reserved_quantity, available_quantity
            FROM inventory_availability
            WHERE item_revision_id = 5 AND location_id = 2
        """)).fetchone()
        assert jb_stock == (Decimal("10"), Decimal("0"), Decimal("10"))

        # Reservations for this PO are now CONSUMED
        pom_ids = session.scalars(
            select(ProductionOrderMaterial.id).where(ProductionOrderMaterial.production_order_id == po_id)
        ).all()
        res_statuses = session.scalars(
            select(StockReservation.status).where(StockReservation.production_order_material_id.in_(pom_ids))
        ).all()
        assert len(res_statuses) == 5
        assert all(s == "CONSUMED" for s in res_statuses)

    # -------------------------------------------------------------------------
    # Step 12: Re-analyze Sales Order Fulfillment: 100% CAN FULFILL FROM STOCK!
    # -------------------------------------------------------------------------
    fa_final_resp = client.get(f"/sales-orders/{order_id}/fulfillment-analysis")
    assert fa_final_resp.status_code == 200
    fa_final = fa_final_resp.json()

    assert fa_final["can_fulfill_entirely_from_stock"] is True
    line_final = fa_final["lines"][0]
    assert Decimal(str(line_final["ordered_quantity"])) == Decimal("100")
    assert Decimal(str(line_final["available_quantity"])) == Decimal("100")
    assert Decimal(str(line_final["ship_from_stock"])) == Decimal("100")
    assert Decimal(str(line_final["production_required"])) == Decimal("0")

    # Step 13: Reserve finished panels for customer order line
    res_payload = {
        "item_revision_id": 1,
        "location_id": 1,
        "sales_order_line_id": order_line_id,
        "quantity": "100",
    }
    res_resp = client.post("/inventory/reservations", json=res_payload)
    assert res_resp.status_code == 201
    assert res_resp.json()["status"] == "ACTIVE"

    # Confirm panel availability in FG-WH is now reserved
    with Session(v1_e2e_engine) as session:
        panel_reserved = session.execute(text("""
            SELECT on_hand_quantity, reserved_quantity, available_quantity
            FROM inventory_availability
            WHERE item_revision_id = 1 AND location_id = 1
        """)).fetchone()
        assert panel_reserved == (Decimal("100"), Decimal("100"), Decimal("0"))
