from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_session_then_chat_round_trip():
    session_resp = client.post("/api/session")
    assert session_resp.status_code == 200
    session_id = session_resp.json()["session_id"]

    chat_resp = client.post("/api/chat", json={"session_id": session_id, "message": "hi"})
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert body["status"] == "route_ready"
    assert body["route"]["google_maps_url"].startswith("https://www.google.com/maps/dir/")


def test_chat_unknown_session_returns_404():
    response = client.post("/api/chat", json={"session_id": "unknown", "message": "hi"})
    assert response.status_code == 404
    assert response.json()["error_code"] == "SESSION_NOT_FOUND"


def test_chat_missing_message_returns_400():
    session_resp = client.post("/api/session")
    session_id = session_resp.json()["session_id"]

    response = client.post("/api/chat", json={"session_id": session_id})
    assert response.status_code == 400
    assert response.json()["error_code"] == "BAD_REQUEST"


def test_route_calculate_endpoint():
    response = client.post(
        "/api/route/calculate",
        json={
            "origin": {"lat": 25.048, "lng": 121.531},
            "destination": {"lat": 25.013, "lng": 121.535},
            "priority_alpha": 0.7,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["distance_m"] > 0
    assert 0 <= body["avg_safety_score"] <= 1
