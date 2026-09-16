"""Product-definition behavior through HTTP against migrated PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from queue import Queue
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from tests.db_support import connect, in_schema, migrate_schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"),
    reason="Use the Docker Compose test service for PostgreSQL endpoint tests.",
)


@pytest.fixture(scope="module")
def product_schema():
    name = "test_products_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            connection.execute("""
                INSERT INTO organizations(id,code,name)
                VALUES (1,'SOLAR','Solar manufacturer'),(2,'OTHER','Other organization')
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture(scope="module")
def api_engine(product_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(product_schema))
    yield engine
    engine.dispose()


def item_and_revision(client, sku="PV-550", kind="FINISHED_GOOD", headers=None):
    response = client.post("/items", json={
        "sku": sku, "name": sku, "item_type": kind, "base_uom": "EA",
    }, headers=headers)
    assert response.status_code == 201, response.text
    item = response.json()
    response = client.post(f"/items/{item['id']}/revisions", json={
        "revision_code": "REV-A",
    }, headers=headers)
    assert response.status_code == 201, response.text
    return item, response.json()


def recipe(client):
    product, revision = item_and_revision(client)
    component, component_revision = item_and_revision(client, "CELL-A", "COMPONENT")
    response = client.post("/boms", json={"product_revision_id": revision["id"]})
    assert response.status_code == 201, response.text
    return product, revision, component, component_revision, response.json()


def add_line(client, bom, component_revision, *, line_no=1, quantity="144"):
    return client.post(f"/boms/{bom['id']}/lines", json={
        "line_no": line_no, "component_revision_id": component_revision["id"], "quantity": quantity,
    })


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def test_create_valid_bom_answers_what_builds_pv550(api):
    product, revision, component, component_revision, bom = recipe(api)
    response = add_line(api, bom, component_revision)
    assert response.status_code == 201, response.text
    assert api.get(f"/items/{product['id']}").json()["sku"] == "PV-550"
    response = api.get(f"/boms/{bom['id']}")
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "DRAFT"
    assert result["product_revision_id"] == revision["id"]
    assert result["product"]["sku"] == "PV-550"
    assert result["product"]["revision_code"] == "REV-A"
    assert Decimal(result["output_quantity"]) == 1
    line = result["lines"][0]
    assert Decimal(line["quantity"]) == 144
    assert line["component"]["item_id"] == component["id"]
    assert line["component"]["sku"] == "CELL-A"
    assert line["component"]["revision_code"] == "REV-A"
    assert line["component"]["base_uom"] == "EA"
    revisions = api.get(f"/items/{product['id']}/revisions").json()
    assert revisions[0]["id"] == revision["id"]
    assert revisions[0]["boms"][0]["id"] == bom["id"]


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity", "0.0000001", "1000000000000"])
def test_reject_invalid_line_quantity(api, quantity):
    *_, component_revision, bom = recipe(api)
    response = add_line(api, bom, component_revision, quantity=quantity)
    assert response.status_code == 422
    assert api.get(f"/boms/{bom['id']}").json()["lines"] == []


def test_accept_smallest_quantity_and_sort_lines(api):
    *_, first_revision, bom = recipe(api)
    _, second_revision = item_and_revision(api, "GLASS-A", "MATERIAL")
    assert add_line(api, bom, first_revision, line_no=2, quantity="0.000001").status_code == 201
    assert add_line(api, bom, second_revision, line_no=1).status_code == 201
    lines = api.get(f"/boms/{bom['id']}").json()["lines"]
    assert [line["line_no"] for line in lines] == [1, 2]
    assert Decimal(lines[1]["quantity"]) == Decimal("0.000001")


def test_reject_unknown_component_and_allow_corrected_retry(api):
    *_, component_revision, bom = recipe(api)
    assert_error(add_line(api, bom, {"id": 9223372036854775807}), 404, "revision_not_found")
    assert add_line(api, bom, component_revision).status_code == 201


def test_reject_duplicate_line_number(api):
    *_, component_revision, bom = recipe(api)
    _, other = item_and_revision(api, "GLASS-A", "MATERIAL")
    assert add_line(api, bom, component_revision).status_code == 201
    assert_error(add_line(api, bom, other), 409, "duplicate_bom_line")
    assert len(api.get(f"/boms/{bom['id']}").json()["lines"]) == 1
    assert add_line(api, bom, other, line_no=2).status_code == 201


def test_reject_duplicate_component_on_different_line(api):
    *_, component_revision, bom = recipe(api)
    assert add_line(api, bom, component_revision).status_code == 201
    assert_error(add_line(api, bom, component_revision, line_no=2), 409, "duplicate_component")


def test_reject_self_reference(api):
    _, revision, _, _, bom = recipe(api)
    assert_error(add_line(api, bom, revision), 422, "bom_self_reference")


@pytest.mark.parametrize("kind", ["MATERIAL", "COMPONENT"])
def test_only_finished_good_can_have_bom(api, kind):
    _, revision = item_and_revision(api, kind=kind)
    assert_error(api.post("/boms", json={"product_revision_id": revision["id"]}), 422, "finished_good_required")


def test_activate_locks_recipe_and_activation_retry_is_safe(api):
    *_, component_revision, bom = recipe(api)
    assert add_line(api, bom, component_revision).status_code == 201
    first = api.post(f"/boms/{bom['id']}/activate")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ACTIVE"
    assert api.post(f"/boms/{bom['id']}/activate").json() == first.json()
    _, other = item_and_revision(api, "GLASS-A", "MATERIAL")
    assert_error(add_line(api, bom, other, line_no=2), 409, "bom_locked")
    assert api.get(f"/boms/{bom['id']}").json() == first.json()
    # A new version is the explicit path for a different recipe.
    response = api.post("/boms", json={"product_revision_id": bom["product_revision_id"], "version": 2})
    assert response.status_code == 201
    assert add_line(api, response.json(), other).status_code == 201


def test_empty_bom_cannot_be_activated(api):
    *_, bom = recipe(api)
    assert_error(api.post(f"/boms/{bom['id']}/activate"), 409, "bom_empty")
    assert api.get(f"/boms/{bom['id']}").json()["status"] == "DRAFT"


def test_duplicate_item_revision_and_bom_have_specific_conflicts(api):
    product, revision, _, _, bom = recipe(api)
    assert_error(api.post("/items", json={
        "sku": product["sku"], "name": "Other", "item_type": "FINISHED_GOOD", "base_uom": "EA",
    }), 409, "duplicate_sku")
    assert_error(api.post(f"/items/{product['id']}/revisions", json={"revision_code": "REV-A"}), 409, "duplicate_revision")
    assert_error(api.post("/boms", json={"product_revision_id": revision["id"]}), 409, "duplicate_bom_version")
    assert api.get(f"/boms/{bom['id']}").status_code == 200


@pytest.mark.parametrize("method,path,body,code", [
    ("get", "/items/99999999", None, "item_not_found"),
    ("get", "/items/99999999/revisions", None, "item_not_found"),
    ("get", "/boms/99999999", None, "bom_not_found"),
    ("post", "/items/99999999/revisions", {"revision_code": "A"}, "item_not_found"),
    ("post", "/boms", {"product_revision_id": 99999999}, "revision_not_found"),
    ("post", "/boms/99999999/lines", {"line_no": 1, "component_revision_id": 99999999, "quantity": 1}, "bom_not_found"),
    ("post", "/boms/99999999/activate", None, "bom_not_found"),
])
def test_missing_records(api, method, path, body, code):
    assert_error(api.request(method, path, json=body), 404, code)


def test_organization_scope_prevents_cross_organization_access(api):
    product, revision, _, component_revision, bom = recipe(api)
    other_headers = {"X-Organization-ID": "2"}
    for path in [f"/items/{product['id']}", f"/items/{product['id']}/revisions", f"/boms/{bom['id']}"]:
        assert api.get(path, headers=other_headers).status_code == 404
    assert api.post(f"/items/{product['id']}/revisions", json={"revision_code": "B"}, headers=other_headers).status_code == 404
    assert api.post("/boms", json={"product_revision_id": revision["id"]}, headers=other_headers).status_code == 404
    assert api.post(f"/boms/{bom['id']}/activate", headers=other_headers).status_code == 404
    assert api.post(f"/boms/{bom['id']}/lines", json={
        "line_no": 1, "component_revision_id": component_revision["id"], "quantity": 1,
    }, headers=other_headers).status_code == 404
    # Same company-specific SKU can legitimately exist in another organization.
    _, other_revision = item_and_revision(api, sku="PV-550", headers=other_headers)
    assert_error(add_line(api, bom, other_revision), 404, "revision_not_found")


def test_unknown_organization(api):
    assert_error(api.get("/items/1", headers={"X-Organization-ID": "99999999"}), 404, "organization_not_found")


def test_header_is_required_and_cannot_be_overridden_in_body(api):
    api.headers.pop("X-Organization-ID")
    assert api.get("/items/1").status_code == 422
    api.headers["X-Organization-ID"] = "1"
    assert api.post("/items", json={
        "sku": "PV", "name": "PV", "item_type": "FINISHED_GOOD", "base_uom": "EA", "organization_id": 2,
    }).status_code == 422


def test_status_is_not_an_arbitrary_bom_update(api):
    _, revision = item_and_revision(api)
    assert api.post("/boms", json={
        "product_revision_id": revision["id"], "status": "ACTIVE",
    }).status_code == 422


@pytest.mark.parametrize("field,value", [("sku", " "), ("name", ""), ("base_uom", " "), ("item_type", "PRODUCT")])
def test_invalid_item_definition(api, field, value):
    payload = {"sku": "PV", "name": "Panel", "item_type": "FINISHED_GOOD", "base_uom": "EA"}
    payload[field] = value
    assert api.post("/items", json=payload).status_code == 422


@pytest.mark.parametrize("field,value", [("version", 0), ("version", True), ("output_quantity", 0), ("product_revision_id", 0)])
def test_invalid_bom_definition(api, field, value):
    _, revision = item_and_revision(api)
    payload = {"product_revision_id": revision["id"]}
    payload[field] = value
    assert api.post("/boms", json=payload).status_code == 422


def test_empty_revision_list_is_distinct_from_unknown_item(api):
    response = api.post("/items", json={
        "sku": "NO-REVISIONS", "name": "Panel", "item_type": "FINISHED_GOOD", "base_uom": "EA",
    })
    assert response.status_code == 201
    revisions = api.get(f"/items/{response.json()['id']}/revisions")
    assert revisions.status_code == 200
    assert revisions.json() == []


def test_concurrent_activation_prevents_late_line_edit(api_engine):
    # Unlike the savepoint fixture, these sessions commit independently.
    backend_pids = Queue()

    def committed_session():
        with Session(api_engine, expire_on_commit=False) as session, session.begin():
            backend_pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            yield session

    app.dependency_overrides[get_session] = committed_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as client:
            *_, component_revision, bom = recipe(client)
            assert add_line(client, bom, component_revision).status_code == 201
            _, other = item_and_revision(client, "CONCURRENT-GLASS", "MATERIAL")
            while not backend_pids.empty():
                backend_pids.get_nowait()
            with api_engine.connect() as blocker, blocker.begin():
                blocker.execute(text("UPDATE boms SET status='ACTIVE' WHERE id=:id"), {"id": bom["id"]})
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(add_line, client, bom, other, line_no=2)
                    try:
                        pid = backend_pids.get(timeout=4)
                        deadline = time.monotonic() + 4
                        while time.monotonic() < deadline:
                            if blocker.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                                break
                            time.sleep(0.02)
                        else:
                            pytest.fail("Line edit did not wait for concurrent activation")
                    finally:
                        blocker.commit()
                    assert_error(pending.result(timeout=8), 409, "bom_locked")
            assert len(client.get(f"/boms/{bom['id']}").json()["lines"]) == 1
    finally:
        app.dependency_overrides.pop(get_session, None)
