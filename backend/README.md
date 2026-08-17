# Safeway Backend（Dev A 部分）

實作 [ForAI.md](../ForAI.md) 第 2、3、4.5、5 節：HTTP 路由、session 轉發、
本地資料層、安全路徑計算引擎、Google Maps URL 產生器。

## 本機啟動

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

`GET /healthz` 應回傳 `{"ok": true}`。

## 測試

```bash
pip install pytest
pytest
```

## 目錄結構

```
backend/
├── inner_interface.py       # 與 Dev B 共用的 ChatService / RouteEngine 介面
├── app/
│   ├── main.py               # FastAPI app、路由掛載、錯誤處理
│   ├── schemas.py            # API request/response models
│   ├── data/                 # 第2節：schema、categories.json/points loader
│   ├── engine/                # 第3節：安全評分、建圖、Dijkstra、LocalDataRouteEngine
│   ├── maps/                  # 第5節：Douglas-Peucker 簡化、Google Maps URL
│   ├── geocoding/             # 第4.4節：Nominatim 地址轉座標
│   ├── chat/                  # ChatService 組裝點 + FakeChatService（假資料）
│   └── routers/                # /api/session、/api/chat、/api/route/calculate
└── data/                      # 範例點位與示範路網（台北車站→公館夜市展示範圍）
```

## 給 Dev B 的整合說明

- `app/chat/dependencies.py` 的 `get_chat_service()` 是唯一組裝點：目前回傳
  `FakeChatService`，實作完 `GeminiChatService` 後直接把這裡換掉即可，
  `app/routers/` 不用改。
- Session 生命週期（建立、對話歷史、動態危險點位暫存、過期清除）完全由
  `ChatService` 實作自己管理（記憶體 dict + TTL）；`session_id` 不存在時
  `handle_message()` 要拋出 `inner_interface.SessionNotFoundError`，
  路由層會接住並轉成 HTTP 404。
- `LocalDataRouteEngine`（`app/engine/route_engine.py`）已實作
  `RouteEngine` 介面，`report_dynamic_hazard` 收集到的 `DynamicHazard`
  可直接傳給 `calculate_route(..., dynamic_hazards=[...])`。

## 尚未定案／已知限制（ForAI.md 2.5）

- 路網資料是備援方案：`data/road_network.json` 手動整理的簡化網格，僅供
  demo，範圍是台北車站到公館夜市之間。要換成 osmnx 擷取的真實 OSM 路網時，
  只需要換掉 `app/engine/graph.py` 的 `RoadGraph.load()` 讀檔來源，其餘
  程式碼不用改。
- Geocoding 預設用免費的 Nominatim（有速率限制），要換 Google Geocoding
  API 只需要在 `app/geocoding/` 新增一個同簽章的函式並在
  `LocalDataRouteEngine.geocode()` 換掉呼叫對象。
