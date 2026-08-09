from fastapi.testclient import TestClient

from superboss.main import create_app


def test_liveness_does_not_require_external_services() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
