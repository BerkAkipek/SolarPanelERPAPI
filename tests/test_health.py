from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_v1_metadata():
    assert app.title == "Solar Panel ERP API"
    assert app.version == "1.0.0"
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()
    assert openapi["info"]["version"] == "1.0.0"
    assert openapi["info"]["title"] == "Solar Panel ERP API"
    tags = [t["name"] for t in openapi.get("tags", [])]
    assert "Product definition" in tags
    assert "Inventory ledger" in tags
    assert "Sales orders" in tags
    assert "Production" in tags
    assert "Production orders" in tags