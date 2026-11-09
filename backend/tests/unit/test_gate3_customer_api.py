"""Customer-facing API denies access and excludes unverified shadow evidence."""
from app.core.config import settings


def test_customer_api_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(settings, "CUSTOMER_API_TOKEN", None)
    response = client.get("/api/v1/predictions/")
    assert response.status_code == 503


def test_customer_api_requires_correct_token(client, monkeypatch):
    monkeypatch.setattr(settings, "CUSTOMER_API_TOKEN", "unit-test-secret")
    assert client.get("/api/v1/predictions/").status_code == 401
    assert client.get(
        "/api/v1/predictions/", headers={"X-Footmo-API-Key": "wrong"}
    ).status_code == 401


def test_empty_verified_customer_api_does_not_fabricate_predictions(client, monkeypatch):
    monkeypatch.setattr(settings, "CUSTOMER_API_TOKEN", "unit-test-secret")
    headers = {"X-Footmo-API-Key": "unit-test-secret"}
    for endpoint in ("/api/v1/predictions/", "/api/v1/matches/", "/api/v1/evaluations/"):
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200
        assert response.json()["items"] == []
    models = client.get("/api/v1/models/", headers=headers)
    assert models.status_code == 200
    assert len(models.json()["models"]) == 2
