### 前後端 API 介面設計
#### 建立對話 Session
```
POST /api/session
```
- 用途：使用者打開對話框、或按下「開始新的路線規劃」時呼叫一次，取得 `session_id`。
- Request body：無（未來若要做多使用者，可傳 `{ "user_id": "..." }`）
- Response：
```json
{
  "session_id": "sess_8f2a1c",
  "created_at": "2026-08-17T20:00:00+08:00"
}
```

#### 傳送訊息／取得回覆
```
POST /api/chat
```
- Request：
```json
{
  "session_id": "sess_8f2a1c",
  "message": "我想從台北車站走到公館夜市，希望盡量安全"
}
```

##### Response
###### `collecting_info` (資訊還沒收集全)
```json
{
  "session_id": "sess_8f2a1c",
  "status": "collecting_info",
  "reply_text": "了解，你希望盡量安全對吧，那大概是想走多快到達呢？"
}
```
###### `route_ready`(收集全了)
```json
{
  "session_id": "sess_8f2a1c",
  "status": "route_ready",
  "reply_text": "幫你規劃了一條比較安全的路線，會經過 1 個派出所跟 14 盞路燈，距離約 1.2 公里。",
  "route": {
    "distance_m": 1240,
    "avg_safety_score": 0.78,
    "alpha_used": 0.7,
    "passed_landmarks": { "street_light": 14, "police_station": 1 },
    "dynamic_hazards_considered": [
      { "category": "fire_incident", "summary": "羅斯福路口火警，已管制", "expires_at": "2026-08-18T02:00:00+08:00" }
    ],
    "google_maps_url": "https://www.google.com/maps/dir/?api=1&origin=25.0478,121.5319&destination=25.0170,121.5340&waypoints=25.0450,121.5330|25.0300,121.5335&travelmode=walking"
  }
}
```
`dynamic_hazards_considered` 是這次計算實際採計了哪些第 2.5 節的動態點位，讓前端可以在回覆中順便告訴使用者「路線有避開哪些即時事件」。
###### `error` (例如地點解析失敗、Gemini 或路徑引擎發生問題）
```json
{
  "session_id": "sess_8f2a1c",
  "status": "error",
  "error_code": "GEOCODING_FAILED",
  "reply_text": "抱歉，我找不到「公館夜市那邊」這個地點，可以講詳細一點的地標或地址嗎？"
}
```
#### 4.5.3 除錯用路徑計算端點
```
POST /api/route/calculate
```
- Request：
```json
{
  "origin": {"lat": 25.0478, "lng": 121.5319},
  "destination": {"lat": 25.0170, "lng": 121.5340},
  "priority_alpha": 0.7,
  "dynamic_hazards": []
}
```
- Response：格式與上面 `route` 物件相同（含 `google_maps_url`）。
#### 4.5.4 錯誤與狀態碼慣例
- 業務邏輯上的失敗（如聽不懂地點、Gemini 回覆判斷資訊不足等）一律回 HTTP 200，用 body 裡的 `status: "error"` 表示，因為請求本身有效，只是這次對話結果是失敗。
- 只有系統層級錯誤才用 HTTP 錯誤碼：`400`（request 格式錯誤，如缺少 `message`）、`404`（`session_id` 不存在）、`500`（後端內部錯誤，如 Gemini API 逾時、路徑引擎例外）。
- 系統層級錯誤統一格式：
```json
{
  "status": "error",
  "error_code": "SESSION_NOT_FOUND",
  "message": "..."
}
```
