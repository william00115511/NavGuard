# 夜間安全導航系統 — 技術規劃報告

**用途**：本文件為黑客松專案之系統設計規劃，供後續 AI 開發 Agent 依此展開實作。技術棧保持中立，僅在必要處提出建議，實際框架選型交由開發階段決定。

**核心情境**：使用者透過對話框描述「要去哪裡」與「重視安全還是速度」，系統結合本地路燈／警局／危險點位等資料，計算出一條偏安全的步行路徑，最終轉換成一組 Google Maps 導航連結交給使用者使用。

**MVP 範圍確認**：本次黑客松僅實作「核心對話 → 路徑計算 → Google Maps URL 輸出」三段，不含地圖視覺化、即時定位、資料管理後台。以下設計以此為邊界，但保留日後擴充這些功能的空間。

---

## 1. 系統架構總覽

系統分為四層，彼此透過明確定義的資料格式溝通，任何一層的內部實作（換框架、換演算法、換模型）都不應影響其他層：

```
┌─────────────────────┐
│  前端對話框 (UI)      │  使用者輸入起訖點與需求描述
└──────────┬───────────┘
           │ 使用者訊息 / 顯示回覆與結果連結
┌──────────▼───────────┐
│ 對話協調層 (Backend)   │  維護對話狀態、呼叫 Gemini API、
│  + Gemini Function    │  處理 Function Calling 觸發
│    Calling            │  （含即時新聞搜尋 → 動態點位回報）
└──────────┬───────────┘
           │ 呼叫 calculate_safe_route(origin, destination, priority)
┌──────────▼───────────┐
│ 路徑計算引擎           │  讀取本地路網 + 安全點位資料，
│ (Path Engine)         │  建圖 → 加權 → 最短路徑演算法
└──────────┬───────────┘
           │ 回傳路徑座標序列 + 統計摘要
┌──────────▼───────────┐
│ 資料層                │  靜態：路燈/警局/危險點位/路網（本地檔案，永久）
│                       │  動態：Gemini 即時搜尋到的新聞點位（session 暫存，有效期限後失效）
│ /data/points/*.json   │  兩者 schema 相同，可擴充，新增資料不需改程式碼
└───────────────────────┘
           │
┌──────────▼───────────┐
│ Google Maps URL 產生器 │  路徑簡化 → 組成中繼點 → 產生連結
└───────────────────────┘
```

**關鍵設計原則**：資料層與運算層分離，運算層與對話層分離。Gemini 只負責「聽懂使用者要什麼」與「觸發計算」，實際的安全評分與路徑演算法完全由後端程式碼決定，不依賴 LLM 計算數值（避免不穩定與幻覺）。

---

## 2. 資料層設計（本地讀檔，可擴充）

### 2.1 資料取得方式（已依你的修改調整）

系統**不在執行期間即時呼叫政府 API**。改為：

1. **離線資料準備階段**（開發期間手動或用腳本執行一次）：從政府開放資料平台（如路燈清冊、警察局地址等）下載原始資料，寫一支轉換腳本，將其標準化為下方統一的點位格式，輸出成本地檔案。
2. **執行期間**：系統啟動時直接讀取 `/data/points/` 目錄下的本地檔案，不對外發送請求，速度快且不受政府 API 穩定性影響。

這支「轉換腳本」與正式系統是分開的一次性工具，日後若要更新資料，重跑腳本覆蓋本地檔案即可。

### 2.2 統一點位資料格式

所有類型的點位（路燈、警局、未來要加的危險點位、監視器，以及第 2.5 節的即時新聞點位等）都用同一個 schema，存成 GeoJSON 或簡化 JSON 皆可，建議如下：

```json
{
  "id": "streetlight_00123",
  "category": "street_light",
  "lat": 25.0478,
  "lng": 121.5319,
  "source": "台北市路燈資料_2026",
  "source_type": "static_local",
  "expires_at": null,
  "confidence": 1.0,
  "meta": {}
}
```

- `source_type`：`static_local`（本地檔案，第 2.1 節的離線資料）或 `dynamic_realtime`（第 2.5 節 Gemini 即時搜尋加入的點位）。
- `expires_at`：靜態點位固定為 `null`（永久有效）；動態點位需要一個到期時間（ISO 時間字串），過期後計算時自動忽略，避免舊新聞一直影響路徑。
- `confidence`：靜態的政府資料視為 1.0；動態的新聞來源點位可以低於 1.0（例如 0.6），讓公式知道這個點位的可信度較低，可用來調降其影響力（見 3.2 節）。

不同類別放在不同檔案（`street_light.json`、`police_station.json`、`danger_zone.json` …），系統啟動時掃描整個目錄，把所有符合 schema 的檔案自動載入。**新增一種點位類型，只需要新增一個資料檔案 + 在下方的「類別設定檔」登記一筆設定，完全不用改程式邏輯**，這是滿足你「之後想加多種正面或負面點位」需求的核心設計。

### 2.3 類別設定檔（可擴充性的關鍵）

新增一個 `categories.json`，定義每個類別對安全分數的「影響方向」（正面/負面）與「影響半徑」，運算層讀這份設定來決定怎麼計分，而不是把邏輯寫死在程式裡：

```json
{
  "street_light":   { "effect": "positive", "weight": 1.0, "radius_m": 30,  "kind": "static" },
  "police_station": { "effect": "positive", "weight": 3.0, "radius_m": 150, "kind": "static" },
  "danger_zone":    { "effect": "negative", "weight": 2.0, "radius_m": 80,  "kind": "static" },
  "fire_incident":  { "effect": "negative", "weight": 4.0, "radius_m": 200, "kind": "dynamic", "default_ttl_hours": 6 },
  "crowd_event":    { "effect": "positive", "weight": 1.5, "radius_m": 100, "kind": "dynamic", "default_ttl_hours": 12 }
}
```

未來要加任何新類別（不管正面還是負面意義），只要在這裡加一行設定 + 對應資料檔（或讓 Gemini 動態回報，見 2.5 節）即可上線，**程式碼完全不需修改**，這也是第 3 節公式能保持通用的原因——公式不認識「路燈」「火災」這些具體名詞，只認識「這個類別是正面還是負面、影響多遠、權重多少」。

### 2.5 動態時效性點位（Gemini 即時新聞搜尋）

除了本地靜態資料，你提到希望 Gemini 能即時上網收集新聞（例如火災、事故、管制區、臨時活動人潮等），把這些有時效性的好/壞點位也加進計算。建議設計：

1. Gemini 在對話過程中，可使用其網路搜尋能力（Gemini API 的 Grounding with Google Search 工具）查找路線經過區域附近「近期」的相關新聞。
2. 找到後，Gemini 透過新的 Function Calling 工具 `report_dynamic_hazard`（定義見 4.2 節）把每一則事件轉成一個點位回報給後端，包含地點描述（由後端或 Gemini 呼叫 Geocoding 轉座標）、類別、正負面、信心程度、有效期限。
3. 後端把這些動態點位暫存在**這次請求/這次對話**的記憶體中（不寫回本地靜態資料檔，避免未經查證的新聞永久污染資料庫），與本地靜態點位合併後一起交給第 3 節的路徑引擎計算。
4. 動態點位一律附帶 `expires_at`，計算時只採計尚未過期的點位；同一場對話結束、或超過有效期後即自動失效。

這樣「本地靜態資料」與「即時動態資料」用完全相同的 schema 與計分邏輯，差別只在資料的生命週期與可信度，公式本身不用為了「即時」這件事另外寫一套邏輯。

### 2.4 路網資料（需額外準備，建議事先確認）

路徑計算需要「道路網路圖」（節點=路口、邊=路段），這部分政府路燈/警局資料無法提供，需要另外準備，建議兩個方案擇一：

- **建議方案**：使用 OpenStreetMap 路網，針對黑客松展示範圍（例如某一行政區）用工具（如 `osmnx`）預先擷取並存成本地圖檔（GraphML / pickle / GeoJSON），一樣放在 `/data/` 目錄，執行期直接讀取，不即時打 API。
- **備援方案**（若時間緊迫）：手動建立簡化的網格圖或幾條主要道路的節點圖，僅供 Demo 使用，精確度較低但開發速度快。

> ⚠️ 這是目前規劃中**唯一還沒有資料來源答案的部分**，建議開發 Agent 在動工前先確認展示範圍（哪個行政區/多大範圍），據此決定路網精細度。

---

## 3. 安全路徑計算引擎（建議演算法）

### 3.1 整體流程

1. 讀取路網圖與所有點位資料。
2. 對每一段道路（graph 中的每條 edge）計算「安全分數」。
3. 將距離與安全分數依使用者的優先程度合併成一個綜合成本（cost）。
4. 用最短路徑演算法（建議 Dijkstra 或 A*）在這個加權圖上找出起訖點間成本最低的路徑。
5. 回傳路徑座標序列與統計摘要（總距離、平均安全分數、經過幾個路燈/警局等）。

### 3.2 Edge 安全分數計算

對每條道路 edge，取其中點（或每隔一定距離取樣點），計算周圍點位的加權影響：

```
edge_safety_score = Σ ( category.weight × decay(distance, category.radius_m) × sign × confidence )
```

其中：
- `sign` 依 `categories.json` 的 `effect` 決定（positive = +1，negative = -1）——這一個符號就是「正面/負面點位」在公式裡的唯一入口，新增任何點位類型都只需要決定它是 +1 還是 -1，不需要改公式結構。
- `decay()` 建議用簡單的線性或高斯衰減函數：距離越近影響越大，超過 `radius_m` 影響趨近 0。
- `confidence` 是第 2.2 節提到的點位可信度（靜態政府資料固定 1.0；Gemini 即時搜尋到的新聞點位可低於 1.0），讓不確定的即時資訊影響力自動打折，不會因為一則未經證實的新聞就大幅改變路徑。
- 計算前會先過濾掉 `expires_at` 已過期的動態點位，等於是把「當下有效」的靜態 + 動態點位放進同一個加總裡。
- 分數需正規化到固定區間（例如 0～1，1 代表最安全），方便後續與距離合併及供 Gemini 回傳數值。

### 3.3 綜合成本函數（安全 vs 速度）

用一個 0～1 的「安全優先權重」`α`（由 Gemini 從對話中萃取，或先給預設值如 0.5）合併距離與安全分數：

```
edge_cost = (1 - α) × normalized_distance + α × (1 - normalized_safety_score)
```

- `α = 0`：完全等同 Google Maps 預設最快路徑。
- `α = 1`：完全以安全為導向，可能繞遠路。
- 中間值：安全與速度的平衡，這也是「後續公式支援回傳數值」的介面——`α` 就是那個可調數值，Gemini 可以直接回傳 0～1 之間的浮點數，不用侷限在「安全優先／速度優先」兩個選項。

### 3.4 演算法選擇

建議使用 **A\***（若路網有座標，用直線距離當 heuristic，效率較 Dijkstra 好）；若時間有限，直接用 **Dijkstra** 亦可，正確性相同，只是效能較低，對黑客松規模的路網通常足夠。

### 3.5 輸出格式（供後續模組使用）

```json
{
  "path_coordinates": [[25.0478,121.5319], [25.0481,121.5325], "..."],
  "distance_m": 1240,
  "avg_safety_score": 0.78,
  "alpha_used": 0.7,
  "passed_landmarks": {"street_light": 14, "police_station": 1}
}
```

---

## 4. 對話與 Gemini Function Calling 整合

### 4.1 對話目標（slot filling）

Gemini 在對話中需要收集以下資訊，收集齊全才觸發 Function Calling：

| 欄位 | 說明 |
|---|---|
| origin | 起點（文字描述，需轉座標，見 4.4） |
| destination | 終點（文字描述，需轉座標） |
| priority_alpha | 0～1 的安全優先權重，可由使用者「安全優先/速度優先/都可以」等語意推斷成數值，也支援使用者直接說「盡量安全」→ 給較高值 |

未收集齊全前，Gemini 應持續追問，不要提前呼叫 Function Calling。三項欄位收集齊全後，Gemini 可先做即時新聞搜尋、回報動態點位（見 4.2b 節），再呼叫 `calculate_safe_route`。

### 4.2 Function Calling 介面定義（建議）

```json
{
  "name": "calculate_safe_route",
  "description": "根據起點、終點與安全優先程度，計算一條夜間步行安全路徑",
  "parameters": {
    "type": "object",
    "properties": {
      "origin": {"type": "string", "description": "起點地址或地標描述"},
      "destination": {"type": "string", "description": "終點地址或地標描述"},
      "priority_alpha": {
        "type": "number",
        "description": "0 到 1 之間，0 代表完全速度優先，1 代表完全安全優先"
      }
    },
    "required": ["origin", "destination", "priority_alpha"]
  }
}
```

後端收到這個 function call 後，實際執行第 3 節的路徑引擎，把結果（含 `path_coordinates`）交回給 Gemini 做總結回覆，同時另外把 `path_coordinates` 交給第 5 節的 URL 產生器。

### 4.2b 動態危險點位回報工具（新增）

對應第 2.5 節的即時新聞需求，額外開一個 Function Calling 工具，讓 Gemini 在對話中查到相關新聞（例如路線附近的火災、事故、封路、大型活動）時可以回報：

```json
{
  "name": "report_dynamic_hazard",
  "description": "回報一個從即時新聞/網路搜尋得知、具時效性的地點事件，會被納入本次路徑安全計算",
  "parameters": {
    "type": "object",
    "properties": {
      "location_description": {"type": "string", "description": "事件地點文字描述，後端會轉成座標"},
      "category": {"type": "string", "description": "事件類別，需對應 categories.json 中 kind=dynamic 的項目，例如 fire_incident"},
      "effect": {"type": "string", "enum": ["positive", "negative"], "description": "對安全的正面或負面影響"},
      "confidence": {"type": "number", "description": "0～1，這則新聞/資訊的可信度"},
      "valid_hours": {"type": "number", "description": "此點位有效時數，未提供則使用該類別的 default_ttl_hours"},
      "summary": {"type": "string", "description": "事件簡述，供回覆使用者時說明"}
    },
    "required": ["location_description", "category", "effect"]
  }
}
```

**建議流程順序**：Gemini 收集完 origin/destination 後、呼叫 `calculate_safe_route` 之前，先視需要搜尋並呼叫 0 至多次 `report_dynamic_hazard`（每個找到的事件呼叫一次），後端把這些點位暫存於本次對話的 session 中；接著才呼叫 `calculate_safe_route`，此時路徑引擎會自動合併「本地靜態點位」＋「本次 session 內尚未過期的動態點位」一起計算（見 3.2 節）。這代表 Gemini 需要能力/工具支援即時網路搜尋（例如 Gemini API 的 Grounding with Google Search），開發 Agent 需確認所用的 Gemini 版本與方案是否支援此功能與其配額限制。

### 4.3 架構與安全性提醒

Gemini API 的呼叫**必須放在後端**，不要把 API Key 放在前端程式碼中，前端只跟你自己的後端溝通，由後端代理呼叫 Gemini。這是為了避免 API 金鑰外洩，開發 Agent 需注意。

### 4.4 待確認：地址轉座標（Geocoding）

使用者輸入的起訖點是文字（例如「台北車站」「公館夜市」），路徑引擎需要的是經緯度座標，中間需要一個地理編碼（Geocoding）步驟，目前規劃中**尚未指定用哪個服務**，建議開發 Agent 在動工前確認：

- 若預算/額度允許：Google Geocoding API（與最終輸出的 Google Maps 一致性最好）。
- 若要免費：OpenStreetMap Nominatim（有使用頻率限制，需注意）。

### 4.5 前後端 API 介面設計

前端**不需要**知道 Gemini 對話、Function Calling、路徑引擎、Google Maps URL 產生器的內部細節，只需要跟後端的「對話端點」互動；後端把「Gemini 對話 → Function Calling → 路徑計算 → URL 產生」這整條流程包成一個黑箱，前後端可以各自獨立開發與測試。

#### 4.5.1 建立對話 Session

因為第 2.5 節的動態點位是綁在「單次對話」的生命週期，需要一個簡單的 session 概念：

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
之後同一輪對話的每則訊息都帶著這個 `session_id`，後端用它來維護對話歷史與第 4.2b 節暫存的動態危險點位。

#### 4.5.2 傳送訊息／取得回覆（核心端點）

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

- Response 依進度不同分三種 `status`：

**A. `collecting_info`**（資訊還沒收集齊全，如 4.1 節的 origin/destination/priority_alpha 三項，Gemini 繼續追問）
```json
{
  "session_id": "sess_8f2a1c",
  "status": "collecting_info",
  "reply_text": "了解，你希望盡量安全對吧，那大概是想走多快到達呢？"
}
```

**B. `route_ready`**（三項欄位收集齊全，`calculate_safe_route` 已執行完成）
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

**C. `error`**（例如地點解析失敗、Gemini 或路徑引擎發生問題）
```json
{
  "session_id": "sess_8f2a1c",
  "status": "error",
  "error_code": "GEOCODING_FAILED",
  "reply_text": "抱歉，我找不到「公館夜市那邊」這個地點，可以講詳細一點的地標或地址嗎？"
}
```

**前端邏輯**：不論哪個 `status`，先把 `reply_text` 當成一則 Gemini 訊息顯示在對話框；若 `status` 為 `route_ready`，額外顯示一張路線摘要卡片，並提供一個「在 Google Maps 開啟導航」按鈕，連到 `route.google_maps_url`。

#### 4.5.3 除錯用路徑計算端點（建議，對應第 8 節開發順序）

第 8 節建議「先獨立驗證路徑引擎，再接 Gemini」，對應這個目標，建議額外開一支不經過 Gemini、直接呼叫路徑引擎的端點，方便前後端分開測試，也可作為日後的「進階模式」保留：

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

> 補充：Gemini 回覆（尤其牽涉即時搜尋時）可能需要數秒，MVP 階段用同步 request/response 即可；若體驗上覺得等待感太明顯，可考慮把 `/api/chat` 改成串流（SSE 或 WebSocket）逐字回覆，此為非必要的加分優化，不影響本節定義的資料結構。

---

## 5. 路徑轉換為 Google Maps URL

### 5.1 URL 格式

使用 Google Maps 的公開 URL 格式（不需 API Key）：

```
https://www.google.com/maps/dir/?api=1
  &origin={起點lat},{起點lng}
  &destination={終點lat},{終點lng}
  &waypoints={wp1_lat},{wp1_lng}|{wp2_lat},{wp2_lng}|...
  &travelmode=walking
```

`travelmode=walking` 建議固定使用，因為這是夜間行人安全導航情境，而非開車。

### 5.2 ⚠️ 重要限制：中繼點數量

這是原始需求中提到「用中繼點確保路徑相近」的關鍵風險，開發 Agent 必須注意：

- Google Maps 的 URL 中繼點**並非無限制**，一般消費端網頁/App 實測穩定支援大約 **9～10 個中繼點**，超過可能被忽略、截斷或導致連結開啟失敗，實際上限建議在開發時實測確認，不要假設可以塞入完整路徑的所有節點。
- 我們的路徑引擎算出來的 `path_coordinates` 可能有數十甚至上百個座標點，**不能直接全部塞進 waypoints**，需要先做路徑簡化。

### 5.3 建議路徑簡化演算法

在把 `path_coordinates` 轉成中繼點之前，先用 **Douglas-Peucker（道格拉斯-普克）演算法** 做線段簡化，保留能代表路徑轉折形狀的關鍵點，去除共線或幾乎共線的中間點，簡化到 9 個點以內（依實測上限調整）。這樣可以在有限的中繼點數量下，盡量讓 Google Maps 實際導航的路徑貼近我們計算出來的安全路徑。

補充提醒：Google Maps 在「相鄰兩個中繼點之間」仍然會用它自己的最快路徑演算法連接，所以簡化時應優先保留「安全路徑明顯偏離最快路徑」的轉折點（例如刻意繞開危險路口的那個轉彎），而不是單純均勻取樣，否則簡化後的路徑可能又跑回 Google 的預設最快路徑。

## 9. 兩人分工與解耦介面（Python ABC）

**分工方式**：

- **Dev A（API Router 等）**：負責 `/api/session`、`/api/chat` 等 HTTP 路由、session 管理、錯誤處理（第 4.5 節），以及路徑計算引擎（第 3 節）、本地資料讀取（第 2 節）、Google Maps URL 產生（第 5 節）的實作。
- **Dev B（Gemini Function Calling 等）**：負責與 Gemini API 對話、slot filling（第 4.1 節）、`calculate_safe_route` / `report_dynamic_hazard` 兩個 Function Calling 工具（第 4.2、4.2b 節）的觸發邏輯。

兩人只透過下面兩個 `abc.ABC` 定義的介面溝通，任一方都不需要等對方寫完就能先用假實作（mock）開發與測試。

```python
# interfaces.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


# ---------- 共用資料結構 ----------

@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


@dataclass(frozen=True)
class DynamicHazard:
    """對應第 2.5 / 4.2b 節：Gemini 即時搜尋到的時效性點位"""
    category: str
    location: LatLng
    effect: str            # "positive" | "negative"
    confidence: float = 1.0
    valid_hours: Optional[float] = None
    summary: str = ""


@dataclass(frozen=True)
class RouteResult:
    """對應第 3.5 節路徑引擎輸出 + 第 5 節 Google Maps URL"""
    path_coordinates: list[LatLng]
    distance_m: float
    avg_safety_score: float
    alpha_used: float
    passed_landmarks: dict[str, int]
    dynamic_hazards_considered: list[DynamicHazard]
    google_maps_url: str


class ChatStatus(str, Enum):
    COLLECTING_INFO = "collecting_info"
    ROUTE_READY = "route_ready"
    ERROR = "error"


@dataclass(frozen=True)
class ChatResult:
    """對應第 4.5.2 節 /api/chat 回應"""
    status: ChatStatus
    reply_text: str
    route: Optional[RouteResult] = None
    error_code: Optional[str] = None


# ---------- ABC #1：API Router ⇄ Gemini/Function-Calling 邊界 ----------
# 由 Dev B 實作；Dev A 的路由層只依賴這個介面，不需知道 Gemini 細節。

class ChatService(ABC):

    @abstractmethod
    def create_session(self) -> str:
        """建立新的對話 session，回傳 session_id。"""
        raise NotImplementedError

    @abstractmethod
    def handle_message(self, session_id: str, message: str) -> ChatResult:
        """
        處理一則使用者訊息（內部含 Gemini 對話與 Function Calling），
        回傳 collecting_info / route_ready / error 三種結果之一。
        """
        raise NotImplementedError


# ---------- ABC #2：Function-Calling ⇄ 路徑計算/資料層 邊界 ----------
# 由 Dev A 實作；Dev B 的 Function Calling 處理常式只依賴這個介面，
# 不需知道路網、安全評分演算法、資料檔案的實作細節。

class RouteEngine(ABC):

    @abstractmethod
    def geocode(self, place_description: str) -> Optional[LatLng]:
        """文字地點轉座標（第 4.4 節），查無結果回傳 None。"""
        raise NotImplementedError

    @abstractmethod
    def calculate_route(
        self,
        origin: LatLng,
        destination: LatLng,
        priority_alpha: float,
        dynamic_hazards: Sequence[DynamicHazard] = (),
    ) -> RouteResult:
        """
        對應第 3 節安全路徑計算 + 第 5 節 Google Maps URL 產生。
        dynamic_hazards 為本次對話中透過 report_dynamic_hazard
        回報、尚未過期的動態點位（第 2.5 節）。
        """
        raise NotImplementedError
```

**平行開發方式**：

- Dev A 先寫一個假的 `FakeChatService(ChatService)`（例如固定回傳一則 `route_ready` 的假資料），把 API Router、session 儲存、錯誤處理都串起來測試，不用等 Dev B 的 Gemini 邏輯完成。
- Dev B 先寫一個假的 `FakeRouteEngine(RouteEngine)`（例如固定回傳一組假座標與假 `google_maps_url`），專心把 Gemini 對話流程、slot filling、兩個 Function Calling 工具的觸發邏輯調通，不用等 Dev A 的路徑演算法完成。
- 兩邊功能都做好後，把各自的真實實作（`GeminiChatService`、`LocalDataRouteEngine` 之類的命名皆可）互相替換掉假物件即可整合，不需要改動對方的程式碼，因為溝通的型別（`ChatResult`、`RouteResult`、`DynamicHazard` 等）已經先講好。

---

*本報告技術棧保持中立，未指定前後端框架，交由開發 Agent 依熟悉度與黑客松時間限制決定。*
