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


def _create_session(**overrides):
    body = {"user_location": _ORIGIN}
    body.update(overrides)
    return client.post("/api/session", json=body)


def _new_session_id() -> str:
    response = _create_session()
    assert response.status_code == 200
    return response.json()["session_id"]


def _chat(**overrides):
    body = {"session_id": _new_session_id(), "message": "hi", "user_location": _ORIGIN}
    body.update(overrides)
    return client.post("/api/chat", json=body)


def test_healthz_reports_loaded_data():
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["graph_loaded"] is True
    assert body["points_loaded"] > 0


def test_create_session_returns_session_id():
    """§6.1：對話前必須先呼叫 POST /api/session 換到 session_id。"""
    response = _create_session()
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["created_at"]


def test_chat_with_freshly_created_session_succeeds():
    """§6.1：session_id 須先由 POST /api/session 配發，才能開始 /api/chat 對話。

    這裡不斷言一定拿到 route_ready：CHAT_SERVICE_BACKEND 若設成 gemini，
    單一句「hi」是否足以觸發路線計算取決於真實模型的判斷，不是這個測試要
    驗證的行為；用 fake 後端跑測試才會穩定拿到 route_ready。
    """
    response = _chat()
    assert response.status_code == 200
    body = response.json()

    assert body["status"] in {"route_ready", "collecting_info", "error"}
    if body["status"] == "route_ready":
        assert "reply_text" not in body  # route_ready 不帶 reply_text（§5.1 修訂）
        assert "route" in body
        assert body["disclaimer"]  # §1 原則 1：每次提供路線都要附免責聲明
        assert body["google_maps_url"].startswith("https://www.google.com/maps/dir/")
        # §6.2 修訂：destination 一律是有意義的地點，一定有 name；
        # 這裡的 origin 用的是 GPS 定位（user_location），不是地標，name 保持 null。
        assert body["origin"]["place_id"] is None
        assert body["origin"]["name"] is None
        assert body["destination"]["name"]
    else:
        assert body["reply_text"]


def test_chat_with_unknown_session_id_returns_404():
    """session_id 不存在或已過期是系統層級錯誤（§6.1 / §6.5），不是自動建立新對話。"""
    response = client.post(
        "/api/chat",
        json={"session_id": "sess_never_issued", "message": "hi"},
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "SESSION_NOT_FOUND"


def test_chat_missing_message_returns_400():
    response = client.post("/api/chat", json={"session_id": _new_session_id()})
    assert response.status_code == 400
    assert response.json()["error_code"] == "BAD_REQUEST"


def test_chat_clear_context_is_idempotent_for_unknown_session_id():
    response = client.post("/api/chat/clear", json={"session_id": "never-seen-before"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_clear_context_then_resumes_fresh():
    session_id = _new_session_id()
    assert _chat(session_id=session_id).status_code == 200

    clear_resp = client.post("/api/chat/clear", json={"session_id": session_id})
    assert clear_resp.status_code == 200

    # 清除後同一個 session_id 再次對話應該照常成功（歷史被清空，但 session 本身還在）。
    assert _chat(session_id=session_id).status_code == 200


def test_route_calculate_returns_single_route_with_polyline():
    response = _calculate()
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "ok"
    assert body["disclaimer"]
    route = body["route"]
    assert route["alpha_used"] == 0.6

    # 前端要靠 path_coordinates 畫 polyline，不能只回 metrics
    assert len(route["path_coordinates"]) >= 2
    assert all(len(p) == 2 for p in route["path_coordinates"])

    metrics = route["metrics"]
    assert metrics["distance_m"] > 0
    assert 0 <= metrics["avg_safety_score"] <= 1
    assert metrics["duration_min_est"] > 0
    assert "street_light" in metrics["data_coverage"]


def test_route_calculate_returns_origin_and_destination_without_names():
    """§6.3 修訂：這個端點直接吃座標、不經文字解析，起訖點固定沒有 place_id/name。"""
    body = _calculate().json()

    for field in ("origin", "destination"):
        point = body[field]
        assert point["lat"] is not None
        assert point["lng"] is not None
        assert point.get("place_id") is None
        assert point.get("name") is None

    assert body["origin"]["lat"] == _ORIGIN["lat"]
    assert body["origin"]["lng"] == _ORIGIN["lng"]
    assert body["destination"]["lat"] == _DESTINATION["lat"]
    assert body["destination"]["lng"] == _DESTINATION["lng"]


def test_route_calculate_alpha_zero_matches_fastest_route():
    """priority_alpha=0 時，回傳的單一路線就是最快路線（detour 為 0）。"""
    body = _calculate(priority_alpha=0.0).json()
    assert body["route"]["alpha_used"] == 0.0
    assert body["route"]["metrics"]["detour_vs_fastest_min"] == 0.0


def test_metrics_use_null_not_zero_for_uncovered_categories():
    """§1 原則 3：沒有覆蓋的類別回 null，不能填 0 或空陣列。"""
    metrics = _calculate().json()["route"]["metrics"]
    assert metrics["lit_coverage_ratio"] is None or metrics["lit_coverage_ratio"] >= 0
    for field in ("police_stations", "convenience_stores", "danger_zones"):
        assert metrics[field] is None or isinstance(metrics[field], list)


def test_route_ready_returns_concrete_police_and_help_point_locations():
    """route_ready 沿線警局／可求助據點／危險點位回傳具體點位（lat/lng），不是統計數量。"""
    metrics = _calculate().json()["route"]["metrics"]
    assert "police_within_150m" not in metrics
    assert "help_points_within_50m" not in metrics
    for field in ("police_stations", "convenience_stores", "danger_zones"):
        points = metrics[field]
        if not points:
            continue
        for point in points:
            assert isinstance(point["lat"], float)
            assert isinstance(point["lng"], float)
            assert "place_id" in point


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
    """§5.4 規則 2：未知類別不報錯，改用 dynamic_unknown 並記一則結構化 warning。"""
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
    assert any(
        w["code"] == "unknown_hazard_category" and w["category"] == "alien_invasion"
        for w in body["warnings"]
    )


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
        return _calculate(priority_alpha=0.0, **kwargs).json()["route"]["metrics"]["avg_safety_score"]

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
