from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 信義區真實 OSM 路網（見 tests/test_pathfinding.py 開頭的節點說明）上的兩個點，
# 中間 fastest 路線會經過 25.030474, 121.565463 這個節點（台北 101 一帶）。
_ORIGIN = {"lat": 25.01848, "lng": 121.557416}
_DESTINATION = {"lat": 25.04478, "lng": 121.584105}
_ON_ROUTE_LOCATION = {"lat": 25.030474, "lng": 121.565463}


def _calculate(**overrides):
    body = {"origin": _ORIGIN, "destination": _DESTINATION, "priority_alpha": 0.6}
    body.update(overrides)
    return client.post("/api/route/calculate", json=body)


def test_healthz_reports_loaded_data():
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["graph_loaded"] is True
    assert body["points_loaded"] > 0


def test_session_then_chat_round_trip():
    session_resp = client.post("/api/session", json={"user_location": _ORIGIN})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["session_id"]

    chat_resp = client.post("/api/chat", json={"session_id": session_id, "message": "hi"})
    assert chat_resp.status_code == 200
    body = chat_resp.json()

    assert body["status"] == "route_ready"
    assert body["selected_route_id"] == "safest"
    assert [r["id"] for r in body["routes"]] == ["safest", "fastest"]
    assert body["disclaimer"]  # §1 原則 1：每次提供路線都要附免責聲明
    assert body["google_maps_url"].startswith("https://www.google.com/maps/dir/")


def test_session_accepts_empty_body():
    assert client.post("/api/session").status_code == 200


def test_chat_unknown_session_returns_404():
    response = client.post("/api/chat", json={"session_id": "unknown", "message": "hi"})
    assert response.status_code == 404
    assert response.json()["error_code"] == "SESSION_NOT_FOUND"


def test_chat_missing_message_returns_400():
    session_id = client.post("/api/session").json()["session_id"]
    response = client.post("/api/chat", json={"session_id": session_id})
    assert response.status_code == 400
    assert response.json()["error_code"] == "BAD_REQUEST"


def test_route_calculate_returns_both_routes_with_polyline():
    response = _calculate()
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "ok"
    assert body["disclaimer"]
    safest, fastest = body["routes"]
    assert (safest["id"], fastest["id"]) == ("safest", "fastest")
    assert fastest["alpha_used"] == 0.0

    # 前端要靠 path_coordinates 畫 polyline，不能只回 metrics
    assert len(safest["path_coordinates"]) >= 2
    assert all(len(p) == 2 for p in safest["path_coordinates"])

    metrics = safest["metrics"]
    assert metrics["distance_m"] > 0
    assert 0 <= metrics["avg_safety_score"] <= 1
    assert metrics["duration_min_est"] > 0
    assert "street_light" in metrics["data_coverage"]
    assert safest["confidence"] in {"high", "medium", "low"}
    assert safest["reasons"]


def test_metrics_use_null_not_zero_for_uncovered_categories():
    """§1 原則 3：沒有覆蓋的類別回 null，不能填 0。"""
    metrics = _calculate().json()["routes"][0]["metrics"]
    for field in ("lit_coverage_ratio", "help_points_within_50m", "police_within_150m"):
        assert metrics[field] is None or metrics[field] >= 0


def test_route_calculate_rejects_out_of_coverage_with_http_200():
    """§6.5：業務邏輯失敗回 HTTP 200 + status error。"""
    response = _calculate(origin={"lat": 35.681, "lng": 139.767})  # 東京車站
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["error_code"] == "OUT_OF_COVERAGE"


def test_route_calculate_rejects_invalid_alpha():
    response = _calculate(priority_alpha=1.5)
    assert response.status_code == 400
    assert response.json()["error_code"] == "BAD_REQUEST"


def test_same_origin_and_destination_does_not_crash():
    response = _calculate(destination={"lat": 25.018481, "lng": 121.557417})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_hazard_category_falls_back_instead_of_being_ignored():
    """§5.4 規則 2：未知類別不報錯，改用 dynamic_unknown 並記一則 warning。"""
    response = _calculate(
        dynamic_hazards=[
            {
                "category": "alien_invasion",
                "location": _ON_ROUTE_LOCATION,
                "summary": "測試用未知類別",
                "confidence": 0.6,
                "valid_hours": 5,
            }
        ]
    )
    assert response.status_code == 200
    body = response.json()

    considered = body["dynamic_hazards_considered"]
    assert len(considered) == 1
    assert considered[0]["category"] == "dynamic_unknown"
    assert any("alien_invasion" in w for w in body["routes"][0]["warnings"])


def test_expired_hazard_is_not_counted():
    response = _calculate(
        dynamic_hazards=[
            {
                "category": "fire_incident",
                "location": _ON_ROUTE_LOCATION,
                "summary": "已經過期的火警",
                "expires_at": "2000-01-01T00:00:00+00:00",
            }
        ]
    )
    assert response.json()["dynamic_hazards_considered"] == []


def test_hazard_lowers_safety_score_of_affected_route():
    """危險點位落在路線上時，該路線的 avg_safety_score 必須下降。"""
    def safety_of(**kwargs):
        routes = _calculate(priority_alpha=0.0, **kwargs).json()["routes"]
        return next(r for r in routes if r["id"] == "fastest")["metrics"]["avg_safety_score"]

    baseline = safety_of()
    with_hazard = safety_of(
        dynamic_hazards=[
            {
                "category": "fire_incident",
                "location": _ON_ROUTE_LOCATION,
                "summary": "路線上的火警",
                "confidence": 1.0,
                "valid_hours": 6,
            }
        ]
    )
    assert with_hazard < baseline
