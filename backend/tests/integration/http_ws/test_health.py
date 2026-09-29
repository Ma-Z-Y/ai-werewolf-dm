from fastapi.testclient import TestClient

import werewolf_dm
from werewolf_dm.interfaces.http_ws.app import create_app


def test_healthz_returns_strict_health_payload() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": werewolf_dm.__version__,
        "active_rooms": 0,
    }
