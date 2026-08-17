# Safeway Backend（Dev A 部分）

實作 [AGENTS.md](../AGENTS.md) §3（資料層）、§4（安全路徑計算引擎）、
§6（後端 API 合約）、§7（Google Maps URL 交接）。

## 本機啟動

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

`GET /healthz` 應回傳 `{"ok": true, "graph_loaded": true, "points_loaded": 18}`。

## 測試

```bash
pytest
```

## 目錄結構

```
backend/
├── inner_interface.py        # §8.2：與 Dev B 共用的 ChatService / RouteEngine 契約
├── app/
│   ├── main.py               # FastAPI app、路由掛載、系統層級錯誤處理（§6.5）
│   ├── config.py             # 路徑常數 + 計分參數（sigmoid k、α 預設值、取樣間隔…）
│   ├── errors.py             # 系統層級錯誤（HTTP 400/404/500/504）
│   ├── schemas.py            # §6 的 request/response models
│   ├── data/                 # §3：統一點位 schema、categories.json/points loader
│   ├── engine/               # §4：安全評分、建圖、A*、metrics、LocalDataRouteEngine
│   ├── maps/                 # §7：Douglas-Peucker 簡化、Google Maps URL
│   ├── geocoding/            # §9.1：Nominatim 地址轉座標（含 1 req/s 節流）
│   ├── chat/                 # ChatService 組裝點 + FakeChatService（假資料）
│   └── routers/              # /api/session、/api/chat、/api/route/calculate
└── data/                     # 示範點位、示範路網、data_sources.md
```

## 設計重點（對應 AGENTS.md 的幾條硬性規定）

- **成本乘上邊長**：`edge_cost = length_m × ((1-α) + α×(1-safety))`（§4.4）。
  少了長度項，演算法會改成最小化 edge 數量，α=0 就不再等同最快路線。
  `tests/test_pathfinding.py` 有針對這點的迴歸測試。
- **不用 min-max 正規化**：改用固定 k 的 sigmoid（§4.3），分數才能跨請求比較。
  `k` 在 `app/config.py` 的 `SAFETY_SIGMOID_K`，拿到真實資料後調一次即可。
- **缺資料不填 0**（§1 原則 3 / §4.7）：某個靜態類別完全沒有覆蓋時，
  `build_scoring_profile()` 會移除該權重、把其餘權重重新正規化、產生一則
  warning，並讓相關 metric 回 `null`。
- **超出覆蓋範圍不外插**：`RoadGraph.nearest_node()` 超過
  `MAX_SNAP_DISTANCE_M` 就 raise `OutOfCoverageError`（§4.7）。
- **靜態分數啟動時算一次**：`EdgeSafetyIndex` 快取靜態 edge 分數，
  每次請求只疊加影響半徑內的動態點位（§4.1）。

## 給 Dev B 的整合說明

- `app/chat/dependencies.py` 的 `get_chat_service()` 是唯一組裝點：目前回傳
  `FakeChatService`，實作完 `GeminiChatService` 後直接把這裡換掉即可，
  `app/routers/` 不用改。
- 介面全部是 **async**（§8.2）。`LocalDataRouteEngine`
  （`app/engine/route_engine.py`）已實作 `RouteEngine`，
  用 `app.engine.route_engine.get_route_engine()` 取得共用單例。
- `DynamicHazard` **沒有** `effect` 欄位：正負面一律查 `categories.json`
  （§5.4 規則 1）。`report_dynamic_hazard` 只要把 geocode 後的座標、類別、
  摘要與 `expires_at` 填好丟給 `calculate_route(..., dynamic_hazards=[...])`。
  未知類別引擎會自動退回 `dynamic_unknown` 並附一則 warning（規則 2）。
- Session 生命週期（建立、對話歷史、動態危險點位暫存、過期清除）完全由
  `ChatService` 實作自己管理（記憶體 dict + TTL，§6.6）；`session_id` 不存在時
  `handle_message()` 要拋出 `inner_interface.SessionNotFoundError`，
  路由層會接住並轉成 HTTP 404。
- 業務層失敗（geocode 失敗、超出覆蓋範圍）請回 `ChatResult(status=ERROR,
  error_code=...)`，不要拋例外——§6.5 規定這類失敗走 HTTP 200。
  常用的 error_code 常數在 `app/errors.py`。

## 尚未定案／已知限制

- 路網資料是 §3.5 的備援方案：`data/road_network.json` 是手動整理的簡化網格，
  僅供 demo，範圍是台北車站到公館夜市之間。要換成 osmnx 擷取的真實 OSM 路網時，
  只需要換掉 `app/engine/graph.py` 的 `RoadGraph.load()` 讀檔來源。
- `data/points/*.json` 全部是示範資料，尚未接上政府開放資料集；
  待辦清單見 `data/data_sources.md`。
- Geocoding 預設用免費的 Nominatim（1 req/s，已內建節流），要換 Google
  Geocoding API 只需要在 `app/geocoding/` 新增一個同簽章的 async 函式，
  並在 `LocalDataRouteEngine.geocode()` 換掉呼叫對象。
- §5.4 規則 4（geocoding 結果過於粗略時 confidence 打 0.5）需要 geocoder 回傳
  結果精細度，目前的 `geocode()` 契約只回座標，這條規則要由 Dev B 的
  handler 端補上，或之後擴充介面。
