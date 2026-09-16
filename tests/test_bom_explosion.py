"""Tests for Step 6: BOM explosion pure domain logic and HTTP interface."""
from decimal import Decimal

import pytest

from app.production.domain import MaterialRequirement, explode_bom


class DummyBOMLine:
    def __init__(self, line_no, comp_id, quantity, sku=None, name=None, uom=None, id=None):
        self.id = id
        self.line_no = line_no
        self.component_revision_id = comp_id
        self.quantity = Decimal(str(quantity))
        self.sku = sku
        self.name = name
        self.base_uom = uom


class DummyBOM:
    def __init__(self, lines, output_quantity=1):
        self.output_quantity = Decimal(str(output_quantity))
        self.lines = lines


def test_one_component():
    bom = DummyBOM(
        lines=[DummyBOMLine(1, 101, "1", sku="JBOX", name="Junction Box", uom="EA")],
        output_quantity=1,
    )
    requirements = explode_bom(bom, 50)
    assert len(requirements) == 1
    req = requirements[0]
    assert req.line_no == 1
    assert req.component_revision_id == 101
    assert req.sku == "JBOX"
    assert req.quantity_per_unit == Decimal("1.000000")
    assert req.required_quantity == Decimal("50.000000")


def test_many_components_solar_panel_example():
    """Matches the exact solar panel example specified by the user:

    PV-550 * 70
    Solar cells    = 10,080
    Glass          = 70
    EVA            = 140
    Junction boxes = 70
    """
    bom = DummyBOM(
        lines=[
            DummyBOMLine(1, 1, "144", sku="CELL", name="Solar cells", uom="EA"),
            DummyBOMLine(2, 2, "1", sku="GLASS", name="Glass", uom="EA"),
            DummyBOMLine(3, 3, "2", sku="EVA", name="EVA", uom="M2"),
            DummyBOMLine(4, 4, "1", sku="JBOX", name="Junction boxes", uom="EA"),
        ],
        output_quantity=1,
    )

    requirements = explode_bom(bom, 70)
    assert len(requirements) == 4

    by_sku = {r.sku: r.required_quantity for r in requirements}
    assert by_sku["CELL"] == Decimal("10080.000000")
    assert by_sku["GLASS"] == Decimal("70.000000")
    assert by_sku["EVA"] == Decimal("140.000000")
    assert by_sku["JBOX"] == Decimal("70.000000")


def test_fractional_quantities():
    bom = {
        "output_quantity": "1",
        "lines": [
            {"line_no": 1, "component_revision_id": 10, "quantity": "0.005325", "sku": "SOLDER"},
            {"line_no": 2, "component_revision_id": 11, "quantity": "0.333333", "sku": "SEALANT"},
        ],
    }
    requirements = explode_bom(bom, "12.5")
    assert len(requirements) == 2

    # 12.5 * 0.005325 = 0.0665625 -> rounds half up to 0.066563
    assert requirements[0].required_quantity == Decimal("0.066563")

    # 12.5 * 0.333333 = 4.1666625 -> rounds half up to 4.166663
    assert requirements[1].required_quantity == Decimal("4.166663")


def test_large_production_quantity():
    bom = DummyBOM(
        lines=[DummyBOMLine(1, 1, "144", sku="CELL")],
        output_quantity=1,
    )
    # 50,000 panels * 144 cells = 7,200,000
    requirements = explode_bom(bom, 50000)
    assert requirements[0].required_quantity == Decimal("7200000.000000")


@pytest.mark.parametrize("invalid_qty", [0, -1, "-10", "-0.000001", "NaN", "Infinity", "-Infinity"])
def test_zero_and_negative_production_rejected(invalid_qty):
    bom = DummyBOM(lines=[DummyBOMLine(1, 1, "1")])
    with pytest.raises(ValueError, match="Production quantity must be"):
        explode_bom(bom, invalid_qty)


def test_bom_batch_output_quantity():
    # Recipe defines a batch of 2 panels requiring 288 cells
    bom = DummyBOM(
        lines=[DummyBOMLine(1, 1, "288", sku="CELL")],
        output_quantity=2,
    )
    # For 5 panels: (5 * 288) / 2 = 720 cells
    requirements = explode_bom(bom, 5)
    assert requirements[0].quantity_per_unit == Decimal("144.000000")
    assert requirements[0].required_quantity == Decimal("720.000000")


def test_pure_function_has_no_side_effects():
    lines = [
        {"line_no": 1, "component_revision_id": 1, "quantity": Decimal("10")},
    ]
    bom = {"output_quantity": Decimal("1"), "lines": lines}

    # Call multiple times with different quantities
    res1 = explode_bom(bom, 5)
    res2 = explode_bom(bom, 10)

    # Input structures remain completely unmutated
    assert bom["output_quantity"] == Decimal("1")
    assert lines[0]["quantity"] == Decimal("10")
    assert res1[0].required_quantity == Decimal("50.000000")
    assert res2[0].required_quantity == Decimal("100.000000")


def test_bom_explosion_http_endpoint():
    """Integration test: calling GET /production/boms/{bom_id}/explode via FastAPI TestClient."""
    import os
    if not os.environ.get("DB_HOST"):
        pytest.skip("Integration test requires DB_HOST")

    from uuid import uuid4
    from fastapi.testclient import TestClient
    from psycopg import sql
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db import get_session
    from app.main import app
    from tests.db_support import connect, in_schema, migrate_schema

    schema_name = "test_bome_" + uuid4().hex
    with connect() as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))

    try:
        migrate_schema(schema_name)
        with in_schema(schema_name) as conn:
            conn.execute("""
                INSERT INTO organizations(id,code,name) VALUES (1,'A','A');
                INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'PV-550','Solar Panel','FINISHED_GOOD','EA'),
                       (2,1,'CELL','Solar Cell','COMPONENT','EA'),
                       (3,1,'GLASS','Solar Glass','COMPONENT','EA');
                INSERT INTO item_revisions(id,organization_id,item_id,revision_code,status)
                VALUES (1,1,1,'REV-A','ACTIVE'),
                       (2,1,2,'REV-A','ACTIVE'),
                       (3,1,3,'REV-A','ACTIVE');
                INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
                VALUES (1,1,1,1,1);
                INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
                VALUES (1,1,1,1,2,144),
                       (2,1,1,2,3,1);
                UPDATE boms SET status='ACTIVE' WHERE id=1;
            """)

        engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(schema_name))

        def test_session():
            with Session(engine, expire_on_commit=False) as s, s.begin():
                yield s

        app.dependency_overrides[get_session] = test_session
        try:
            with TestClient(app, headers={"X-Organization-ID": "1"}) as client:
                resp = client.get("/production/boms/1/explode?quantity=70")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["bom_id"] == 1
                assert Decimal(data["production_quantity"]) == Decimal("70")
                reqs = {r["sku"]: Decimal(r["required_quantity"]) for r in data["requirements"]}
                assert reqs["CELL"] == Decimal("10080.000000")
                assert reqs["GLASS"] == Decimal("70.000000")
        finally:
            app.dependency_overrides.pop(get_session, None)
            engine.dispose()
    finally:
        with connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))

