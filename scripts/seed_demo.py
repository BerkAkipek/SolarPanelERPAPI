"""Seed script for Solar Panel ERP API V1.0.0.

Populates the database with realistic solar manufacturing demo data:
- Organization: Solaria Dynamics Inc.
- Customer Partner: Helios Energy Solutions
- Locations: FG-WH (Warehouse), RAW-WH (Warehouse), QA-HOLD (Quarantine), PROD-01 (Production)
- Finished Good: PV-550 (550W Half-Cell Monocrystalline Solar Panel)
- Components: CELL-M10 (144), GLASS-3.2 (1), EVA-SHEET (2), JB-1500 (1), FRAME-68 (1)
- Active BOM for PV-550
- Starting Physical Stock:
    - 30 PV-550 in FG-WH
    - 12,000 CELL-M10 in RAW-WH
    - 100 GLASS-3.2 in RAW-WH
    - 200 EVA-SHEET in RAW-WH
    - 50 JB-1500 in RAW-WH
    - 20 JB-1500 in QA-HOLD (quarantine - excluded from availability!)
    - 100 FRAME-68 in RAW-WH
- Sales Order: SO-2026-0001 (100 panels PV-550 for Helios Energy Solutions)

Demonstrating the core V1 business question:
- Ordered: 100 panels
- Available from stock: 30 panels
- Production required: 70 panels
- Material feasibility: Shortage of 20 JB-1500 (since 20 are quarantined)
"""
import sys
from decimal import Decimal
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import Organization
from app.sales.models import BusinessPartner, SalesOrder, SalesOrderLine
from app.products.models import Item, ItemRevision, BOM, BOMLine
from app.inventory.models import InventoryLocation, InventoryMovement, InventoryMovementLine


def seed_demo_data():
    engine = get_engine()

    with Session(engine) as session:
        # 1. Organization
        org = session.scalar(select(Organization).where(Organization.code == "SOLARIA"))
        if not org:
            org = Organization(code="SOLARIA", name="Solaria Dynamics Inc.")
            session.add(org)
            session.flush()
            print(f"[+] Created Organization: {org.name} (ID: {org.id})")
        else:
            print(f"[*] Organization already exists: {org.name} (ID: {org.id})")

        org_id = org.id

        # 2. Business Partner (Customer)
        customer = session.scalar(
            select(BusinessPartner).where(
                BusinessPartner.organization_id == org_id,
                BusinessPartner.code == "HELIOS",
            )
        )
        if not customer:
            customer = BusinessPartner(
                organization_id=org_id,
                code="HELIOS",
                name="Helios Energy Solutions",
            )
            session.add(customer)
            session.flush()
            print(f"[+] Created Customer Partner: {customer.name} (ID: {customer.id})")
        else:
            print(f"[*] Customer partner already exists: {customer.name} (ID: {customer.id})")

        # 3. Locations
        locations_data = [
            ("FG-WH", "Finished Goods Warehouse", "WAREHOUSE"),
            ("RAW-WH", "Raw Material Warehouse", "WAREHOUSE"),
            ("QA-HOLD", "Quarantine Inspection Bay", "QUARANTINE"),
            ("PROD-01", "Module Assembly Line 1", "PRODUCTION"),
        ]
        locations = {}
        for code, name, loc_type in locations_data:
            loc = session.scalar(
                select(InventoryLocation).where(
                    InventoryLocation.organization_id == org_id,
                    InventoryLocation.code == code,
                )
            )
            if not loc:
                loc = InventoryLocation(
                    organization_id=org_id,
                    code=code,
                    name=name,
                    location_type=loc_type,
                    is_active=True,
                )
                session.add(loc)
                session.flush()
                print(f"[+] Created Location: {code} - {name} ({loc_type})")
            locations[code] = loc

        # 4. Items & Revisions
        items_data = [
            ("PV-550", "550W Half-Cell Monocrystalline Solar Panel", "FINISHED_GOOD", "EA", "NONE"),
            ("CELL-M10", "182mm Monocrystalline Solar Cell", "COMPONENT", "EA", "NONE"),
            ("GLASS-3.2", "3.2mm Solar Tempered Glass Sheet", "COMPONENT", "EA", "NONE"),
            ("EVA-SHEET", "Solar Encapsulant EVA Film Sheet", "COMPONENT", "EA", "NONE"),
            ("JB-1500", "IP68 1500V Split Junction Box", "COMPONENT", "EA", "NONE"),
            ("FRAME-68", "Anodized Aluminium Frame 35mm", "COMPONENT", "EA", "NONE"),
        ]
        revisions = {}
        for sku, name, item_type, uom, tracking in items_data:
            item = session.scalar(
                select(Item).where(Item.organization_id == org_id, Item.sku == sku)
            )
            if not item:
                item = Item(
                    organization_id=org_id,
                    sku=sku,
                    name=name,
                    item_type=item_type,
                    base_uom=uom,
                    tracking_type=tracking,
                )
                session.add(item)
                session.flush()
                print(f"[+] Created Item: {sku} ({item_type})")

            rev = session.scalar(
                select(ItemRevision).where(
                    ItemRevision.organization_id == org_id,
                    ItemRevision.item_id == item.id,
                    ItemRevision.revision_code == "REV-A",
                )
            )
            if not rev:
                rev = ItemRevision(
                    organization_id=org_id,
                    item_id=item.id,
                    revision_code="REV-A",
                    status="ACTIVE",
                )
                session.add(rev)
                session.flush()
                print(f"[+] Created Revision: {sku} REV-A (ACTIVE)")
            revisions[sku] = rev

        # 5. Bill of Materials (BOM) for PV-550 REV-A
        pv_rev = revisions["PV-550"]
        bom = session.scalar(
            select(BOM).where(
                BOM.organization_id == org_id,
                BOM.product_revision_id == pv_rev.id,
                BOM.version == 1,
            )
        )
        if not bom:
            bom = BOM(
                organization_id=org_id,
                product_revision_id=pv_rev.id,
                version=1,
                output_quantity=Decimal("1"),
                status="DRAFT",
            )
            session.add(bom)
            session.flush()

            bom_lines_data = [
                (1, revisions["CELL-M10"].id, Decimal("144")),
                (2, revisions["GLASS-3.2"].id, Decimal("1")),
                (3, revisions["EVA-SHEET"].id, Decimal("2")),
                (4, revisions["JB-1500"].id, Decimal("1")),
                (5, revisions["FRAME-68"].id, Decimal("1")),
            ]
            for line_no, comp_rev_id, qty in bom_lines_data:
                bline = BOMLine(
                    organization_id=org_id,
                    bom_id=bom.id,
                    line_no=line_no,
                    component_revision_id=comp_rev_id,
                    quantity=qty,
                )
                session.add(bline)
            session.flush()

            bom.status = "ACTIVE"
            session.flush()
            print(f"[+] Created Active BOM v1 for PV-550 with 5 recipe lines")
        else:
            print(f"[*] BOM v1 for PV-550 already exists")

        # 6. Physical Stock Movements
        movement = session.scalar(
            select(InventoryMovement).where(
                InventoryMovement.organization_id == org_id,
                InventoryMovement.reference == "DEMO-INIT-STOCK",
            )
        )
        if not movement:
            movement = InventoryMovement(
                organization_id=org_id,
                movement_type="RECEIPT",
                reference="DEMO-INIT-STOCK",
            )
            session.add(movement)
            session.flush()

            stock_lines = [
                (revisions["PV-550"].id, locations["FG-WH"].id, Decimal("30")),
                (revisions["CELL-M10"].id, locations["RAW-WH"].id, Decimal("12000")),
                (revisions["GLASS-3.2"].id, locations["RAW-WH"].id, Decimal("100")),
                (revisions["EVA-SHEET"].id, locations["RAW-WH"].id, Decimal("200")),
                (revisions["JB-1500"].id, locations["RAW-WH"].id, Decimal("50")),
                (revisions["JB-1500"].id, locations["QA-HOLD"].id, Decimal("20")),  # Quarantined
                (revisions["FRAME-68"].id, locations["RAW-WH"].id, Decimal("100")),
            ]
            for rev_id, loc_id, qty in stock_lines:
                mline = InventoryMovementLine(
                    organization_id=org_id,
                    movement_id=movement.id,
                    item_revision_id=rev_id,
                    to_location_id=loc_id,
                    quantity=qty,
                )
                session.add(mline)
            session.flush()
            print("[+] Seeded initial inventory movements across warehouses and quarantine bay")
        else:
            print("[*] Initial inventory movement already exists")

        # 7. Sales Order SO-2026-0001
        so = session.scalar(
            select(SalesOrder).where(
                SalesOrder.organization_id == org_id,
                SalesOrder.order_number == "SO-2026-0001",
            )
        )
        if not so:
            so = SalesOrder(
                organization_id=org_id,
                order_number="SO-2026-0001",
                customer_id=customer.id,
                currency_code="USD",
                status="CONFIRMED",
            )
            session.add(so)
            session.flush()

            soline = SalesOrderLine(
                organization_id=org_id,
                sales_order_id=so.id,
                line_no=1,
                item_revision_id=revisions["PV-550"].id,
                quantity=Decimal("100"),
                unit_price=Decimal("180.00"),
            )
            session.add(soline)
            session.flush()
            print(f"[+] Created Sales Order SO-2026-0001 (100x PV-550) for {customer.name}")
        else:
            print(f"[*] Sales Order SO-2026-0001 already exists")

        session.commit()

        # Print summary
        print("\n" + "=" * 70)
        print(" Solar Panel ERP API v1.0.0 - Demo Data Ready")
        print("=" * 70)
        print(f" Organization: {org.name} (Header: X-Organization-ID: {org_id})")
        print(f" Sales Order:  {so.order_number} (Status: {so.status})")
        print(" Demanded:     100 panels of PV-550 (550W Module)")
        print(" On-Hand:      30 panels available in Finished Goods Warehouse (FG-WH)")
        print(" Production:   70 panels required")
        print(" Feasibility:  Shortage of 20 Junction Boxes (JB-1500)")
        print("               (50 available in RAW-WH, 20 held in QA-HOLD quarantine)")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    try:
        seed_demo_data()
    except Exception as err:
        print(f"[-] Error seeding demo data: {err}", file=sys.stderr)
        sys.exit(1)
