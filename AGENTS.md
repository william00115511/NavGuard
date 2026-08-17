# AGENTS.md — Safeway 夜間步行安全導航

> 這份文件是給 AI 助手（以及新加入的開發者）讀的專案上下文。閱讀後應該能理解：這個專案在做什麼、架構怎麼切、每個模組的輸入輸出契約是什麼、哪些事情絕對不能做。
>
> 專案性質：24 小時 Dev Jam，Gemini API 賽道。後端 FastAPI + Gemini Function Calling，前端 Flutter。

---

## 0. 一句話定義

使用者透過對話說出「我想從 A 走到 B，希望安全一點」，系統在**自建的本地路網圖**上，以夜間照明、可求助據點、危險點位加權計算，回傳一條**可解釋原因**的較安全步行路線，並與最快路線並列比較。

---

## 1. 不可違反的原則

這些是設計約束，任何實作不得繞過：

1. **安全分數是輔助決策，不是安全保證。** UI 必須持續顯示免責聲明與「遇到緊急危險請撥當地緊急電話」。禁止出現「絕對安全」「保證安全」等文案。
2. **Gemini 不得產生任何安全數值。** 分數、距離、路徑、點位密度一律由後端 deterministic 程式碼計算。Gemini 只負責：聽懂需求、觸發工具、把已算好的結果轉成自然語言。禁止讓 LLM 算數值（避免幻覺）。
3. **缺資料絕不填 0。** 某類資料在該區域沒有覆蓋時，做法是：移除該項權重並重新正規化其餘權重、降低 `confidence`、在 `warnings` 明確告知使用者，而不是當作「該區沒有風險」。
4. **API Key 只能在後端。** Gemini key 與任何 server-side key 不得出現在 Flutter 程式碼、`--dart-define`、APK 或 Git 中。Flutter 端只保留有 application restriction（Android package + SHA-1 / iOS bundle ID）的 Maps SDK key。
5. **不處理受害者個資，不在地圖上畫精確歷史犯罪位置。** 犯罪資料一律以 grid／密度形式呈現。
6. **MVP 只覆蓋一個城市／行政區**，不得宣稱資料覆蓋全世界。範圍外的請求要明確回覆「此區尚未覆蓋」。

---

## 2. 系統架構總覽

四層，層間以明確資料格式溝通；任一層內部換框架／演算法／模型都不影響其他層。

```text
┌──────────────────────────────────────────────┐
│ Flutter App                                  │
│ - google_maps_flutter 畫 polyline 與 marker  │
│ - geolocator 取目前位置作為預設起點          │ ← Frontend Dev
│ - 對話框 UI（訊息氣泡 + 路線摘要卡片）       │
└──────────────────┬───────────────────────────┘
                   │ HTTPS  POST /api/session, /api/chat
                   ▼
┌──────────────────────────────────────────────┐
│ FastAPI Backend                              │
│ ┌──────────────────────────────────────────┐ │
│ │ API Router：路由、session、錯誤處理      │ │  ← Dev A
│ └──────────────────┬───────────────────────┘ │
│                    │ ChatService (ABC)       │
│ ┌──────────────────▼───────────────────────┐ │
│ │ 對話協調層：Gemini Function Calling      │ │  ← Dev B
│ │ - slot filling                           │ │
│ │ - 即時新聞搜尋 → 動態點位回報            │ │
│ └──────────────────┬───────────────────────┘ │
│                    │ RouteEngine (ABC)       │
│ ┌──────────────────▼───────────────────────┐ │
│ │ 路徑計算引擎：建圖 → 加權 → A*           │ │  ← Dev A
│ │ + Google Maps URL 產生器                 │ │
│ └──────────────────┬───────────────────────┘ │
└────────────────────┼─────────────────────────┘
                     ▼
     /data/  本地檔案（靜態，永久）
     ├── graph/          OSM 路網圖（osmnx 預先擷取）
     ├── points/*.json   路燈／警局／危險點位／可求助據點
     └── categories.json 類別設定
                     +
     session 記憶體（動態，Gemini 即時新聞點位，過期失效）
```

**關鍵原則**：資料層與運算層分離，運算層與對話層分離。前端也不需知道 Gemini／Function Calling／路徑引擎的內部細節，只跟後端的對話端點互動。

---

## 3. 資料層

### 3.1 資料取得方式

**不在執行期即時呼叫政府 API。** 離線階段：從政府開放資料下載原始資料 → 轉換腳本標準化為統一點位格式 → 輸出本地檔案。執行期系統啟動時直接讀 `/data/points/` 本地檔案，不對外請求。轉換腳本與正式系統分開，日後更新資料重跑腳本覆蓋本地檔案即可。

同時建立 `data_sources.md` 記錄每份資料的 URL、授權、覆蓋範圍、更新日期——這會直接回傳給使用者作為資料透明度佐證。

### 3.2 統一點位資料格式

所有類型點位（路燈、警局、危險點位、監視器、可求助據點、即時新聞點位）共用同一 schema：

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

- `source_type`：`static_local`（本地離線資料）或 `dynamic_realtime`（3.4 節 Gemini 即時搜尋加入）
- `expires_at`：靜態固定 `null`（永久）；動態需 ISO 時間字串，過期後計算自動忽略
- `confidence`：靜態政府資料固定 1.0；動態新聞來源可 < 1.0（如 0.6），讓公式知道可信度較低而自動打折

不同類別各自一檔案（`street_light.json`、`police_station.json`、`danger_zone.json`…），啟動時掃描整個目錄自動載入符合 schema 的檔案。**新增點位類型只需新增資料檔 + 在 `categories.json` 登記一筆設定，完全不用改程式邏輯。**

### 3.3 類別設定檔 `categories.json`

定義每個類別對安全分數的影響方向與影響半徑。運算層讀此設定計分，不寫死任何類別名稱：

```json
{
  "street_light":   { "effect": "positive", "weight": 1.0, "radius_m": 30,  "kind": "static" },
  "police_station": { "effect": "positive", "weight": 3.0, "radius_m": 150, "kind": "static" },
  "help_point":     { "effect": "positive", "weight": 2.0, "radius_m": 80,  "kind": "static" },
  "danger_zone":    { "effect": "negative", "weight": 2.0, "radius_m": 80,  "kind": "static" },
  "fire_incident":  { "effect": "negative", "weight": 4.0, "radius_m": 200, "kind": "dynamic", "default_ttl_hours": 6 },
  "crowd_event":    { "effect": "positive", "weight": 1.5, "radius_m": 100, "kind": "dynamic", "default_ttl_hours": 12 },
  "dynamic_unknown":{ "effect": "negative", "weight": 1.0, "radius_m": 100, "kind": "dynamic", "default_ttl_hours": 3 }
}
```

公式不認識具體類別名稱，只認識「正面／負面、影響半徑、權重」。未來加任何新類別，只要加一行設定 + 對應資料檔即可。

`help_point`：夜間仍營業、可明確求助的地點（24h 超商、藥局、飯店大廳）。若城市不開放路燈資料，此類別是主要的照明 proxy。

`dynamic_unknown`：Gemini 回報了 `categories.json` 中不存在的類別時的 fallback（見 5.4）。

### 3.4 動態時效性點位（Gemini 即時新聞搜尋）

1. Gemini 在對話中用網路搜尋能力（Grounding with Google Search）查路線附近「近期」相關事件（火災、事故、管制區、臨時人潮等）
2. 找到後透過 `report_dynamic_hazard`（5.4 節）把每則事件回報後端
3. 後端把地點描述 geocode 成座標，暫存於**這次對話**的記憶體，與靜態點位合併交給引擎計算
4. **不寫回本地靜態檔**，避免未經查證的新聞永久污染資料庫
5. 動態點位一律附 `expires_at`，計算時只採計未過期點位；對話結束或超過有效期自動失效

靜態與動態資料用完全相同的 schema 與計分邏輯，差別只在生命週期與可信度。

### 3.5 路網資料

路徑計算需道路網路圖（節點 = 路口，邊 = 路段）。政府路燈／警局資料無法提供，需另外準備：

- **主要方案**：OSM 路網，針對展示範圍用 `osmnx` 預先擷取（`network_type="walk"`）存本地圖檔（GraphML／pickle），放 `/data/graph/`，執行期直接讀取。
- **備援方案**（時間緊迫）：手動建簡化網格圖或幾條主要道路節點圖，僅供 Demo。

⚠️ **動工前必須先確認展示範圍**（哪個行政區／多大範圍），據此決定路網精細度。這是目前唯一的前置阻塞項。

---

## 4. 安全路徑計算引擎

### 4.1 整體流程

```text
啟動時（一次性）
  讀路網圖 → 讀所有靜態點位 → 對每條 edge 預算靜態安全原始分數 → 快取

每次請求
  取本次 session 未過期的動態點位
    → 只對受影響半徑內的 edge 疊加動態分數（不重算全圖）
    → 正規化為 0~1 safety
    → 依 α 合成 edge cost
    → A* 找起訖點間成本最低路徑
    → 同時以 α=0 算一條最快路線作為對照
    → 回傳兩條路線 + metrics + reasons + warnings
```

### 4.2 Edge 安全原始分數

對每條 edge 沿線每隔固定距離（建議 25m）取樣，計算樣本點周圍點位的加權影響後取平均：

```text
raw_score(edge) = mean over samples of
    Σ ( category.weight × decay(distance, category.radius_m) × sign × confidence )
```

- `sign`：依 `categories.json` 的 `effect` 決定（positive = +1，negative = −1）。這是正負面點位進入公式的**唯一入口**，新增任何點位類型只需決定 +1 或 −1。
- `decay()`：線性或高斯衰減，距離越近影響越大，超過 `radius_m` 趨近 0。
- `confidence`：靜態 1.0；動態新聞點位可 < 1.0。
- 計算前先過濾掉已過期的動態點位。

### 4.3 正規化為 0~1

**不使用 min-max 正規化。** 原因：min-max 的基準會隨每次請求加入的動態點位而漂移，導致同一條路在不同請求得到不同分數，無法解釋也無法測試。

改用**固定參數的 squashing 函式**，讓分數跨請求可比較：

```text
safety(edge) = 1 / (1 + exp(-k × raw_score(edge)))     # k 為調參常數，寫在 config
```

結果 0~1，1 最安全。`k` 在準備資料時依實際分數分布調一次即可固定。

### 4.4 綜合成本函數（安全 vs 速度）

```text
edge_cost = length_m × ( (1 - α) + α × (1 - safety(edge)) )
```

⚠️ **成本必須乘上邊長**。若 cost 是每條 edge 的固定值而非「每公尺代價」，最短路徑演算法會偏好「edge 數量少」的路徑而非「距離短」的路徑——OSM 路網中一條長幹道可能是一條 edge，十條短巷也是十條 edge，不乘長度會嚴重失真。

- `α` = 0：`edge_cost = length_m`，完全等同最短距離路線（這就是我們的「最快路線」對照組，不需要外部 API）
- `α` = 1：`edge_cost = length_m × (1 - safety)`，完全安全導向，可能繞遠路
- 中間值為平衡；`α` 是 0~1 連續浮點數，不侷限於「安全優先／速度優先」兩選項
- 預設值 0.6

### 4.5 演算法

使用 **A\***。Heuristic 用起訖點直線距離，但**必須乘上最小每公尺成本才是 admissible**：

```text
h(n) = haversine(n, goal) × (1 - α)
```

因為 `safety ≤ 1` 時每公尺成本下界為 `(1 - α)`。少了這個係數的 A* 會高估，可能回傳非最佳路徑。時間有限時直接用 Dijkstra 也可以，黑客松規模的路網通常足夠快。

### 4.6 路線層級的可解釋 metrics

引擎除了路徑本身，還要產出給使用者看的、可驗證的指標。這些是**報告用**，不參與路徑選擇：

| metric | 定義 |
|---|---|
| `distance_m` | 路徑總長 |
| `duration_min_est` | 以 1.3 m/s 步行速度估算 |
| `avg_safety_score` | 各 edge safety 以長度加權平均 |
| `lit_coverage_ratio` | 採樣點中 30m 內有路燈資料的比例（無路燈資料時為 `null`） |
| `help_points_within_50m` | 沿線 50m 內可求助據點數 |
| `police_within_150m` | 沿線 150m 內警察局數 |
| `passed_landmarks` | 各類別經過數量的 dict |
| `detour_vs_fastest_min` | 相較 α=0 路線多花的分鐘數 |
| `data_coverage` | 本次實際有資料的類別清單 |

### 4.7 confidence 與 warnings

- `confidence` 取 `high` / `medium` / `low`：依 `data_coverage` 涵蓋比例與動態點位佔比決定
- 任何類別無覆蓋 → 該權重從公式移除、其餘權重重新正規化、產生一則 `warning`，例如：
  `"路燈資料在此區沒有覆蓋，未將照明納入評分"`
- 起訖點超出路網範圍 → 回錯誤，不做外插

---

## 5. 對話與 Gemini Function Calling

### 5.1 對話目標（slot filling）

| 欄位 | 必填 | 說明 |
|---|---|---|
| `origin` | 是（但可由 GPS 自動帶入） | 起點文字描述或座標 |
| `destination` | 是 | 終點文字描述 |
| `priority_alpha` | **否** | 0~1 安全優先權重，未表態時用預設 0.6 |

⚠️ **`priority_alpha` 不列為必填 slot。** 強迫使用者回答「你希望多安全」會產生尷尬的追問（「那你大概想走多快？」），而且使用者通常答不出來。正確做法：origin 與 destination 齊全就直接算，α 用預設值；若使用者訊息中含有「盡量安全」「趕時間」等語意，Gemini 才調整 α。路線回傳後使用者可再說「再安全一點」，重算即可。

### 5.2 執行順序

```text
收集 origin / destination
  → （視需要）Google Search 搜尋路線附近近期事件
  → 對每則事件呼叫一次 report_dynamic_hazard（0 至多次）
  → 呼叫 calculate_safe_route
  → 用回傳結果撰寫自然語言回覆
```

### 5.3 Function：`calculate_safe_route`

```json
{
  "name": "calculate_safe_route",
  "description": "根據起點、終點與安全優先程度，計算一條夜間步行安全路徑，並同時回傳最快路線作為對照",
  "parameters": {
    "type": "object",
    "properties": {
      "origin": {"type": "string", "description": "起點地址或地標描述；使用者未指定時填 \"current_location\""},
      "destination": {"type": "string", "description": "終點地址或地標描述"},
      "priority_alpha": {"type": "number", "description": "0到1之間，0代表完全速度優先，1代表完全安全優先。使用者未明確表態時省略此參數"}
    },
    "required": ["origin", "destination"]
  }
}
```

### 5.4 Function：`report_dynamic_hazard`

```json
{
  "name": "report_dynamic_hazard",
  "description": "回報一個從即時新聞或網路搜尋得知、具時效性的地點事件，會被納入本次路徑安全計算",
  "parameters": {
    "type": "object",
    "properties": {
      "location_description": {"type": "string", "description": "事件地點文字描述，需盡量精確到路口或門牌，後端會轉成座標"},
      "category": {"type": "string", "description": "事件類別，需對應 categories.json 中 kind=dynamic 的項目，例如 fire_incident"},
      "confidence": {"type": "number", "description": "0~1，這則資訊的可信度"},
      "valid_hours": {"type": "number", "description": "此點位有效時數，未提供則使用該類別的 default_ttl_hours"},
      "summary": {"type": "string", "description": "事件簡述，供回覆使用者時說明"},
      "source_url": {"type": "string", "description": "新聞來源連結"}
    },
    "required": ["location_description", "category", "summary"]
  }
}
```

**後端處理規則（重要）**：

1. **`effect` 不由 Gemini 決定。** 正負面一律查 `categories.json`，避免 LLM 與設定檔互相矛盾。舊版把 `effect` 列為必填參數是設計錯誤。
2. **未知 category** → 不報錯，改用 `dynamic_unknown` 類別（負面、低權重、TTL 3 小時），並記一則 warning。
3. **Geocoding 失敗** → 丟棄該點位，記一則 warning，不中斷流程。
4. **Geocoding 結果過於粗略**（只解析到行政區層級而非路口／門牌）→ 保留但 `confidence` 乘 0.5。新聞常出現「羅斯福路口」這種模糊描述，若直接以 200m 負面半徑套用，會誤傷大片路網。
5. 同一 session 內重複回報同地點同類別 → 去重。

### 5.5 System Instruction

```text
你是 Safeway 的夜間步行安全導航助手，使用繁體中文。

你必須呼叫工具取得所有地理與安全資料。你不得編造路燈位置、營業時間、
警方位置、犯罪統計、安全分數或任何形式的安全保證。工具回傳什麼數值，
你就說什麼數值。

資料缺漏時，要照工具回傳的 warnings 清楚告訴使用者哪些因素沒有被納入評分。

不要說「這條路很安全」，要說「這條路線的照明與可求助據點較多」。
每次提供路線時，都要提醒這是輔助建議而非安全保證。

若使用者表示自己正遭遇危險，立即停止一般導航流程，優先建議聯絡當地
緊急服務、前往最近明亮且有人員的公共場所。
```

### 5.6 已知技術風險

⚠️ **Google Search grounding 與 function calling 是否能在同一次請求同時啟用，依 Gemini 版本與 API 方案而異。** 開發第一件事就是驗證這點。若不支援，改用兩段式：第一次呼叫只開 grounding 做搜尋、拿回文字結果；第二次呼叫關掉 grounding、只開 function calling，把搜尋結果放進 context 讓模型抽取事件。這會影響 Dev B 的實作結構，越早確認越好。

模型以環境變數 `GEMINI_MODEL` 指定（demo 建議 `gemini-2.5-flash`），以便換模型不改程式碼。

---

## 6. 後端 API 合約

前端只需跟對話端點互動，後端把整條流程包成黑箱，前後端可各自獨立開發測試。

### 6.1 `POST /api/session`

使用者打開對話框或按「開始新的路線規劃」時呼叫一次。

Request：
```json
{ "user_location": {"lat": 25.0330, "lng": 121.5654} }
```
`user_location` 選填，用於 geocoding bias 與 `origin: "current_location"` 的解析。

Response：
```json
{ "session_id": "sess_8f2a1c", "created_at": "2026-08-17T20:00:00+08:00" }
```

### 6.2 `POST /api/chat`（核心端點）

Request：
```json
{
  "session_id": "sess_8f2a1c",
  "message": "我想從台北車站走到公館夜市，希望盡量安全",
  "user_location": {"lat": 25.0330, "lng": 121.5654}
}
```

Response 依進度分三種 `status`：

**A. `collecting_info`**
```json
{
  "session_id": "sess_8f2a1c",
  "status": "collecting_info",
  "reply_text": "好的，你現在人在台北車站附近嗎？還是要從別的地方出發？"
}
```

**B. `route_ready`**
```json
{
  "session_id": "sess_8f2a1c",
  "status": "route_ready",
  "reply_text": "幫你規劃了一條較安全的路線，會經過 1 個派出所跟 5 個營業中的可求助據點，比最快路線多走約 4 分鐘。羅斯福路口目前有火警管制，路線已避開。這是依公開資料的輔助建議，無法保證安全。",
  "disclaimer": "此建議依公開資料與即時資訊產生，無法保證安全；緊急狀況請立即撥打 110 或 119。",
  "selected_route_id": "safest",
  "routes": [
    {
      "id": "safest",
      "label": "推薦的較安全路線",
      "path_coordinates": [[25.0478, 121.5319], [25.0481, 121.5325], "..."],
      "alpha_used": 0.6,
      "confidence": "medium",
      "metrics": {
        "distance_m": 1420,
        "duration_min_est": 18,
        "avg_safety_score": 0.78,
        "lit_coverage_ratio": 0.71,
        "help_points_within_50m": 5,
        "police_within_150m": 1,
        "passed_landmarks": {"street_light": 14, "police_station": 1},
        "detour_vs_fastest_min": 4,
        "data_coverage": ["street_light", "police_station", "help_point"]
      },
      "reasons": [
        "沿途 5 個營業中可求助據點",
        "比最快路線多 4 分鐘，但避開 2 段照明不足路段"
      ],
      "warnings": []
    },
    {
      "id": "fastest",
      "label": "最快路線",
      "path_coordinates": ["..."],
      "alpha_used": 0.0,
      "confidence": "medium",
      "metrics": { "distance_m": 1180, "duration_min_est": 14, "avg_safety_score": 0.52, "...": "..." },
      "reasons": [],
      "warnings": ["部分路段缺乏路燈資料，照明未納入評分"]
    }
  ],
  "dynamic_hazards_considered": [
    {
      "category": "fire_incident",
      "summary": "羅斯福路口火警，已管制",
      "confidence": 0.6,
      "expires_at": "2026-08-18T02:00:00+08:00",
      "source_url": "https://..."
    }
  ],
  "google_maps_url": "https://www.google.com/maps/dir/?api=1&origin=...&travelmode=walking"
}
```

**C. `error`**
```json
{
  "session_id": "sess_8f2a1c",
  "status": "error",
  "error_code": "GEOCODING_FAILED",
  "reply_text": "抱歉，我找不到「公館夜市那邊」這個地點，可以講詳細一點的地標或地址嗎？"
}
```

常見 `error_code`：`GEOCODING_FAILED`、`OUT_OF_COVERAGE`（起訖點超出路網範圍）、`NO_ROUTE_FOUND`、`UPSTREAM_TIMEOUT`。

**前端行為**：不論哪個 status，先把 `reply_text` 當作助手訊息顯示在對話框；`route_ready` 時額外在地圖畫出 `routes` 的 polyline（推薦路線與最快路線用不同顏色）、標出 `passed_landmarks` 的 marker，並顯示可展開的路線摘要卡片與「在 Google Maps 開啟導航」按鈕。

### 6.3 `POST /api/route/calculate`（除錯／進階模式）

不經 Gemini，直接呼叫路徑引擎，方便前後端分開測試，也可保留作日後「進階模式」。

Request：
```json
{
  "origin": {"lat": 25.0478, "lng": 121.5319},
  "destination": {"lat": 25.0170, "lng": 121.5340},
  "priority_alpha": 0.6,
  "dynamic_hazards": []
}
```
`dynamic_hazards` 此處直接吃**座標**（`DynamicHazard` 內部型別），因為 geocoding 屬於 Function Calling handler 的職責，不屬於引擎。

Response：與 6.2 的 `routes` / `dynamic_hazards_considered` 部分相同結構。

### 6.4 `GET /healthz`

回傳 `{"ok": true, "graph_loaded": true, "points_loaded": 12043}`。

### 6.5 錯誤與狀態碼慣例

- **業務邏輯失敗**（聽不懂地點、資訊不足、超出覆蓋範圍）一律回 HTTP 200，body 用 `status: "error"`。請求本身有效，只是這次對話結果失敗。
- **系統層級錯誤**才用 HTTP 錯誤碼：`400`（request 格式錯）、`404`（`session_id` 不存在）、`500`（後端內部錯誤）、`504`（Gemini 或 geocoding 逾時）。統一格式：
  ```json
  { "status": "error", "error_code": "SESSION_NOT_FOUND", "message": "..." }
  ```

### 6.6 Session 儲存

MVP 用 process 內記憶體 dict（`session_id` → 對話歷史 + 動態點位）。這代表**後端必須是單一 process**；多 instance 部署會導致 session 隨機遺失。加 TTL（建議 30 分鐘）避免記憶體無限增長。

### 6.7 延遲

Gemini 回覆（尤其牽涉即時搜尋 + 多次 geocoding）可能需數秒。MVP 用同步 request/response 即可，前端顯示 loading 狀態。若等待感明顯，可把 `/api/chat` 改成 SSE 串流，屬加分項，不影響本節資料結構。

---

## 7. Google Maps URL 交接（次要功能）

因為前端自己會畫 polyline，**路線視覺化不依賴 Google Maps**。這個 URL 只是「使用者想改用 Google Maps 實際導航」時的交接管道，屬便利功能而非核心輸出。

### 7.1 格式

不需 API Key 的公開 URL：
```text
https://www.google.com/maps/dir/?api=1
  &origin={lat},{lng}
  &destination={lat},{lng}
  &waypoints={lat},{lng}|{lat},{lng}|...
  &travelmode=walking
```
`travelmode=walking` 固定使用。

### 7.2 中繼點數量限制

- Google Maps URL 的 waypoints **並非無限制**，一般消費端網頁／App 實測穩定支援約 **9~10 個**，超過可能被忽略、截斷或連結開啟失敗。開發時實測確認，不要假設可塞入完整路徑。
- 引擎算出的 `path_coordinates` 可能有數十甚至上百點，**不能直接全塞進 waypoints**。

### 7.3 路徑簡化

用 **Douglas-Peucker 演算法**做線段簡化，保留代表路徑轉折形狀的關鍵點，簡化到 9 點以內。

⚠️ Google Maps 在相鄰兩中繼點之間仍用它自己的最快路徑演算法連接，所以簡化時應**優先保留「安全路徑明顯偏離最短路徑」的轉折點**（例如刻意繞開危險路口的那個轉彎），而非均勻取樣，否則簡化後路徑會又跑回 Google 的預設路線。實作上：先算出 α=0 的最短路徑，找出兩條路徑分歧最大的幾個點，強制保留。

### 7.4 UI 提醒

點擊「在 Google Maps 開啟」時，需提示使用者：Google Maps 的實際路線可能與推薦的安全路線略有出入。

---

## 8. 分工與解耦介面

### 8.1 分工

| | 負責範圍 |
|---|---|
| **Dev A** | `/api/session`、`/api/chat`、`/api/route/calculate`、`/healthz` 等 HTTP 路由、session 管理、錯誤處理（§6）＋ 路徑計算引擎（§4）＋ 本地資料讀取與轉換腳本（§3）＋ Google Maps URL 產生（§7） |
| **Dev B** | 與 Gemini API 對話、slot filling（§5.1）、`calculate_safe_route` 與 `report_dynamic_hazard` 兩個工具的觸發與後處理邏輯（§5.3、§5.4）、system instruction 調校 |

兩人只透過兩個 `interfaces.py` 介面溝通，任一方不需等對方寫完就能先用假實作（mock）開發測試。

### 8.3 平行開發方式

- **Dev A** 先寫 `FakeChatService(ChatService)`（固定回傳一則 `route_ready` 假資料），把 API Router、session 儲存、錯誤處理串起來測試，不等 Dev B。
- **Dev B** 先寫 `FakeRouteEngine(RouteEngine)`（固定回傳假座標、假 metrics、假 URL），專心把 Gemini 對話流程、slot filling、兩個工具的觸發邏輯調通，不等 Dev A 的演算法。
- 兩邊做好後，把各自真實實作（`GeminiChatService`、`LocalGraphRouteEngine`）替換掉假物件即可整合，不需改動對方程式碼——因為溝通型別已先講好。

---

## 9. 未定案事項

### 9.1 Geocoding 服務（阻塞 Dev A 與 Dev B）

使用者輸入與 Gemini 回報的地點都是文字，引擎需要座標，中間需 Geocoding：

- **預算允許**：Google Geocoding API（與 Google Maps 交接一致性最好）
- **免費**：OpenStreetMap Nominatim（有頻率限制，1 req/s，需設 User-Agent；動態點位可能一次要查多筆，要注意排隊）

無論選哪個，都要對結果做**覆蓋範圍檢查**：解析出的座標若不在路網範圍內，直接回 `OUT_OF_COVERAGE`。

### 9.2 展示範圍（阻塞所有人）

必須先確定是哪個城市／行政區、多大範圍。這決定 osmnx 抓多大的圖、要下載哪些政府資料集、以及路燈資料是否存在。

### 9.3 路燈資料可得性

若選定城市不開放路燈資料，`street_light` 類別留空，`lit_coverage_ratio` 回 `null`，以 `help_point` 密度作為照明 proxy，並在每次回覆顯示 warning。**不得默默把沒資料當成沒風險。**
