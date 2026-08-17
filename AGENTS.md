# AGENTS.md — Safeway 夜間步行安全導航

> 這份文件是給 AI 助手（以及新加入的開發者）讀的專案上下文。閱讀後應該能理解：這個專案在做什麼、架構怎麼切、每個模組的輸入輸出契約是什麼、哪些事情絕對不能做。
>
> 專案性質：24 小時 Dev Jam，Gemini API 賽道。後端 FastAPI + Gemini Function Calling，前端 Flutter。

---

## 0. 一句話定義

使用者透過對話說出「我想從 A 走到 B，希望安全一點」，系統在**自建的本地路網圖**上，以夜間照明、可求助據點、危險點位加權計算，回傳一條依前端安全參數（`priority_alpha`）算出的**可解釋原因**的較安全步行路線（結構化數值，而非後端組好的文字敘述）；引擎內部仍以最快路線作對照算出額外時間等指標，只是不再對外並列回傳兩條路線。

---

## 1. 不可違反的原則

這些是設計約束，任何實作不得繞過：

1. **安全分數是輔助決策，不是安全保證。** UI 必須持續顯示免責聲明與「遇到緊急危險請撥當地緊急電話」。禁止出現「絕對安全」「保證安全」等文案。
2. **Gemini 不得產生任何安全數值。** 分數、距離、路徑、點位密度一律由後端 deterministic 程式碼計算。Gemini 只負責：聽懂需求、觸發工具、把已算好的結果轉成自然語言。禁止讓 LLM 算數值（避免幻覺）。
3. **缺資料絕不填 0。** 某類資料在該區域沒有覆蓋時，做法是：移除該項權重並重新正規化其餘權重、在 `warnings` 明確告知使用者，而不是當作「該區沒有風險」。
4. **API Key 只能在後端。** Gemini key 與任何 server-side key 不得出現在 Flutter 程式碼、`--dart-define`、APK 或 Git 中。Flutter 端只保留有 application restriction（Android package + SHA-1 / iOS bundle ID）的 Maps SDK key。
5. **不處理受害者個資，不在地圖上畫精確歷史犯罪位置。** 犯罪資料一律以 grid／密度形式呈現。
6. **MVP 只覆蓋一個城市／行政區**，不得宣稱資料覆蓋全世界。範圍外的請求要明確回覆「此區尚未覆蓋」。
7. **Gemini 免費層級必備多模型動態降級鏈**。後端 Gateway 必須配置容錯降級鏈（如 `gemini-3.7-flash` -> `gemini-3.1-flash-lite` -> `gemini-flash-latest`），防止 Preview 模型 20 RPD 配額耗盡中斷服務。
8. **Cloud Run 後端 Server Key 與 iOS Client Key 嚴格分離**。後端地點解析（Places API）必須使用無限制 Server Key，嚴禁複用綁定 iOS Bundle ID 的 Client Key。

---

## 2. 系統架構總覽

四層，層間以明確資料格式溝通；任一層內部換框架／演算法／模型都不影響其他層。

```text
┌──────────────────────────────────────────────┐
│ Flutter App                                  │
│ - google_maps_flutter 畫 polyline 與 marker  │
│ - geolocator 取目前位置作為預設起點          │
│ - 對話框 UI（訊息氣泡 + 路線摘要卡片）       │
│ - 安全／速度優先滑桿（priority_alpha）       │
└──────────────────┬───────────────────────────┘
                   │ HTTPS  POST /api/chat, /api/chat/clear
                   ▼
┌──────────────────────────────────────────────┐
│ FastAPI Backend                              │
│ ┌──────────────────────────────────────────┐ │
│ │ API Router：路由、session、錯誤處理      │ │
│ └──────────────────┬───────────────────────┘ │
│                    │ ChatService (ABC)       │
│ ┌──────────────────▼───────────────────────┐ │
│ │ 對話協調層：Gemini Function Calling      │ │
│ │ - slot filling（origin / destination）   │ │
│ │ - 即時新聞搜尋 → 動態點位回報            │ │
│ └──────────────────┬───────────────────────┘ │
│                    │ RouteEngine (ABC)       │
│ ┌──────────────────▼───────────────────────┐ │
│ │ 路徑計算引擎：建圖 → 加權 → A*           │ │
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
    → 引擎回傳兩條路線 + metrics + warnings（供內部比較與 §7 URL 簡化）；
      API 層只對外回傳其中一條，見 §6.2 修訂
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
| `help_points` | 沿線 50m 內可求助據點的**具體點位列表**（`id`/`lat`/`lng`/`name`），該類別無資料時為 `null`（§6.2 修訂） |
| `police_stations` | 沿線 150m 內警察局的**具體點位列表**，格式同上，該類別無資料時為 `null`（§6.2 修訂） |
| `passed_landmarks` | 其餘類別（路燈、危險區域等）經過數量的 dict；`police_station`／`help_point` 已用上面兩個具體點位列表取代，不再重複列在這裡計數 |
| `detour_vs_fastest_min` | 相較 α=0 路線多花的分鐘數 |
| `data_coverage` | 本次實際有資料的類別清單 |

⚠️ **修訂**：舊版 `help_points_within_50m`／`police_within_150m` 只回數量，前端沒辦法在地圖上標出實際位置。改成回傳具體點位列表後，前端要顯示數量時自己對列表 `length`，後端不再重複提供一個數字欄位。

### 4.7 warnings

- 任何類別無覆蓋 → 該權重從公式移除、其餘權重重新正規化、產生一則結構化 `warning`：
  `{ "code": "missing_data_category", "category": "street_light" }`（前端自行查表組文案，見 §6.2）
- 起訖點超出路網範圍 → 回錯誤，不做外插

⚠️ **修訂**：拔掉 `route.confidence`（`high`/`medium`/`low`）欄位——這個值只是
把 `data_coverage` 與動態點位佔比再摘要成一個粗略等級，實際判斷仍要看
`warnings` 裡的 `missing_data_category`，`confidence` 沒有提供 `warnings`
給不了的資訊，是多餘的重複表達。`route_ready` 回應不再帶這個欄位。

---

## 5. 對話與 Gemini Function Calling

### 5.1 對話目標（slot filling）

| 欄位 | 必填 | 說明 |
|---|---|---|
| `origin` | 是（但可由 GPS 自動帶入） | 起點文字描述或座標 |
| `destination` | 是 | 終點文字描述 |

⚠️ **`priority_alpha`（安全優先權重）不是 slot filling 的一部分，由前端直接提供，不經 Gemini 判斷。** 前端用 UI（例如滑桿）讓使用者自己調整安全 vs 速度的權重，每次 `/api/chat` 請求都帶上這個數值（§6.2），未調整時預設 0.6；後端觸發 `calculate_safe_route`（§5.3）時直接代入這個值。原因：強迫使用者在對話中回答「你希望多安全」會產生尷尬的追問（「那你大概想走多快？」），使用者通常也答不出來；讓 Gemini 從語氣猜測數值更是不精確、不可重現。改由前端 UI 提供，使用者可隨時滑動重算，也更符合「安全相關數值一律 deterministic」的精神（原則 2）。

### 5.2 執行順序

```text
收集 origin / destination
  → （視需要）Google Search 搜尋路線附近近期事件
  → 對每則事件呼叫一次 report_dynamic_hazard（0 至多次）
  → 呼叫 calculate_safe_route
  → 工具回傳後直接結束這一輪：結果是結構化資料（§6.2 route_ready），
    不需要 Gemini 再撰寫自然語言回覆
```

### 5.3 Function：`calculate_safe_route`

```json
{
  "name": "calculate_safe_route",
  "description": "根據起點與終點，計算一條夜間步行安全路徑，並同時回傳最快路線作為對照。安全優先權重（priority_alpha）由前端直接提供，不是這個工具的參數，也不由 Gemini 決定（見 §5.1）",
  "parameters": {
    "type": "object",
    "properties": {
      "origin": {"type": "string", "description": "起點地址或地標描述；使用者未指定時填 \"current_location\""},
      "destination": {"type": "string", "description": "終點地址或地標描述"}
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

⚠️ **修訂**：`calculate_safe_route` 工具回傳結果後，後端不再多打一次 Gemini
把數值轉成文字（§6.2 的 `route_ready` 不帶 `reply_text`，資料已經是結構化
JSON，前端自己組文案）。上面「不要說『這條路很安全』」這類措辭規範，現在
只約束 `collecting_info`／`error` 這兩種仍然需要 Gemini 產生對話文字的情境。

⚠️ **修訂（地點消歧）**：新增一條規則，讓 Gemini 在呼叫 `calculate_safe_route`
前自行判斷連鎖品牌／多分店地標是否需要追問。能從既有資訊（另一端地點、
使用者提過的城市或區域、分店彼此距離很近）合理判斷時直接選用該分店繼續，
不追問；只有真的無法判斷時才詢問，且只列出少數幾個（2～4 個）最可能的
選項，不列出全部分店。實際文字見 `google_genai_gateway.py` 的
`SYSTEM_INSTRUCTION`（本節範例文字未同步逐字更新，以程式碼為準）。
---

## 6. 後端 API 合約

前端只需跟對話端點互動，後端把整條流程包成黑箱，前後端可各自獨立開發測試。

### 6.1 `session_id`（先呼叫 `POST /api/session` 交換）

⚠️ **修訂**：曾經試過改用固定裝置編號 `client_id`（`"1"`～`"N"`，不需交換
就能直接對話），但展示場景之外的部署裝置數不固定、前端也一直維持著呼叫
`POST /api/session` 的實作，兩邊契約對不上，所以改回兩段式交握：

- 前端必須先呼叫 `POST /api/session`（選填 `user_location`），後端動態配發
  一個新的 `session_id`（格式 `sess_` + 12 碼 hex，不是連續整數，避免被
  猜測到別人的 session）：
  ```json
  { "session_id": "sess_ac1726bf8f93", "created_at": "2026-08-18T02:00:00+08:00" }
  ```
- 之後每次呼叫 `POST /api/chat`（§6.2）都要帶上這個 `session_id`。
  `session_id` 不存在或已過期（超過 TTL 未使用，或被 §6.6 的定時回收清掉）
  時，後端回系統層級 `404 SESSION_NOT_FOUND`（§6.5）——這不是業務邏輯失敗，
  前端收到後要重新呼叫 `POST /api/session` 換一個新的 `session_id`，不是
  重試原請求。
- 使用者想「開始新的路線規劃」（清空對話歷史，但沿用同一個 `session_id`）
  時，前端呼叫 `POST /api/chat/clear`（§6.2.1），不需要換新的
  `session_id`；對不存在或已過期的 `session_id` 呼叫一律視為 no-op、回
  `200`，不是錯誤——這是使用者主動要求的清空動作，沒有「session 必須先
  存在」的前提。

### 6.2 `POST /api/chat`（核心端點）

Request：
```json
{
  "session_id": "sess_ac1726bf8f93",
  "message": "我想從台北車站走到公館夜市，希望盡量安全",
  "user_location": {"lat": 25.0330, "lng": 121.5654},
  "priority_alpha": 0.6
}
```
`session_id` 見 §6.1，須先呼叫 `POST /api/session` 換到。`priority_alpha` 由前端滑桿等 UI 直接提供（§5.1），選填，未帶時後端用預設值 0.6；這個值不經 Gemini，後端觸發 `calculate_safe_route` 時直接代入。

Response 依進度分三種 `status`：

**A. `collecting_info`**
```json
{
  "status": "collecting_info",
  "reply_text": "好的，你現在人在台北車站附近嗎？還是要從別的地方出發？"
}
```

**B. `route_ready`**——不帶 `reply_text`。路上遇到的警局、求助據點、額外時間等數值資料已經是 §4.6 的結構化 `metrics`，警告訊息也是結構化的 `code`（見下方 warning 說明），前端自行組文案／多語系，不需要後端組好的中文句子。同一個原因，這裡也只回傳**一條路線**——依前端這次帶的 `priority_alpha` 算出來的那一條，不再像舊版一樣把 `fastest` 對照組路線一起塞進回應（引擎內部仍會算 `fastest` 供 `detour_vs_fastest_min` 與 §7 URL 簡化使用，只是不對外暴露成第二條路線）：
```json
{
  "status": "route_ready",
  "disclaimer": "此建議依公開資料與即時資訊產生，無法保證安全；緊急狀況請立即撥打 110 或 119。",
  "route": {
    "path_coordinates": [[25.0478, 121.5319], [25.0481, 121.5325], "..."],
    "alpha_used": 0.6,
    "metrics": {
      "distance_m": 1420,
      "duration_min_est": 18,
      "avg_safety_score": 0.78,
      "lit_coverage_ratio": 0.71,
      "help_points": [
        {"id": "helppoint_00123", "lat": 25.0479, "lng": 121.5321, "name": "7-Eleven"}
      ],
      "police_stations": [
        {"id": "police_00042", "lat": 25.0481, "lng": 121.5327, "name": "大安分局"}
      ],
      "passed_landmarks": {"street_light": 14},
      "detour_vs_fastest_min": 4,
      "data_coverage": ["street_light", "police_station", "help_point"]
    },
    "warnings": [
      { "code": "missing_data_category", "category": "danger_zone" }
    ]
  },
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
`route.warnings` 的 `code` 目前有三種，`category`／`summary` 依 code 才有值：
| code | 說明 | 附帶欄位 |
|---|---|---|
| `missing_data_category` | 某靜態類別在此區沒有覆蓋，未納入評分（§4.7） | `category`：類別 key（如 `street_light`） |
| `unknown_hazard_category` | Gemini 回報的即時事件類別未登記，已用 `dynamic_unknown` 低權重計入（§5.4 規則 2） | `category`：原始（未登記的）類別 key |
| `hazard_expired` | 即時事件已過期，未納入本次計算 | `summary`：該事件的簡述 |

**C. `error`**
```json
{
  "status": "error",
  "error_code": "GEOCODING_FAILED",
  "reply_text": "抱歉，我找不到「公館夜市那邊」這個地點，可以講詳細一點的地標或地址嗎？"
}
```

常見 `error_code`：`GEOCODING_FAILED`、`OUT_OF_COVERAGE`（起訖點超出路網範圍）、`NO_ROUTE_FOUND`、`UPSTREAM_TIMEOUT`。

**前端行為**：`collecting_info` 與 `error` 把 `reply_text` 當作助手訊息顯示在對話框；`route_ready` 沒有 `reply_text`，前端依 `route.metrics` 與 `route.warnings` 自行組文案、在地圖畫出 `route.path_coordinates` 的 polyline、標出 `passed_landmarks` 的 marker，並顯示可展開的路線摘要卡片與「在 Google Maps 開啟導航」按鈕。

### 6.2.1 `POST /api/chat/clear`

使用者按「開始新的路線規劃」、想清掉目前對話歷史時呼叫，不需要換新的 `session_id`（§6.1）。

Request：
```json
{ "session_id": "sess_ac1726bf8f93" }
```

Response：
```json
{ "status": "ok" }
```
對不存在或已經過期的 `session_id` 呼叫一樣回 200（視為 no-op），不是錯誤。

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

Response：與 6.2 `route_ready` 的 `route` / `dynamic_hazards_considered` 部分相同結構（外層多一個 `status: "ok"`）。

### 6.4 `GET /healthz`

回傳 `{"ok": true, "graph_loaded": true, "points_loaded": 12043}`。

### 6.5 錯誤與狀態碼慣例

- **業務邏輯失敗**（聽不懂地點、資訊不足、超出覆蓋範圍）一律回 HTTP 200，body 用 `status: "error"`。請求本身有效，只是這次對話結果失敗。
- **系統層級錯誤**才用 HTTP 錯誤碼：`400`（request 格式錯）、`404`（`session_id` 不存在或已過期，見 §6.1）、`500`（後端內部錯誤）、`504`（Gemini 或 geocoding 逾時）。統一格式：
  ```json
  { "status": "error", "error_code": "BAD_REQUEST", "message": "..." }
  ```

### 6.6 Session 儲存與定時回收

MVP 用 process 內記憶體 dict（`session_id` → 對話歷史 + 動態點位 + `user_location` + `last_access_at`），這代表**後端必須是單一 process**；多 instance 部署會導致對話歷史隨機遺失。

`session_id` 由 `POST /api/session`（§6.1）動態配發，數量不固定，所以沒有
「一次配好 N 個」這種預先分配，靠 TTL 過期 + 背景定時回收控制記憶體用量：

- **`session_ttl_seconds`**（可設定，建議 30 分鐘）：對話閒置超過這個時間
  即視為過期。過期判斷有兩層：
  1. **存取觸發**：`POST /api/chat` 拿到已過期的 `session_id` 時，直接回
     `404 SESSION_NOT_FOUND`（§6.5），不會靜默重置成新對話——因為
     `session_id` 是動態配發的亂數字串而非固定裝置編號，過期後應該讓前端
     明確重新走一次 `POST /api/session` 交握，而不是讓同一個 ID 在背景悄悄
     換了一份新對話。
  2. **定時回收**：後端啟動時起一個背景排程（`session_reap_interval_seconds`，
     可設定，建議 5 分鐘），週期性掃過所有 session、清掉已過期的，釋放記憶體。
     這一層不依賴任何請求觸發，就算某個 `session_id` 建立後完全沒有後續
     請求，也會在下一次排程時被回收，避免記憶體隨時間無限成長。
- 使用者主動要清掉歷史時用 `POST /api/chat/clear`（§6.2.1）：只清空該
  `session_id` 的對話歷史，`session_id` 本身、TTL 計時都保留，不需要等
  TTL 也不需要重新交握。

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

- Google Maps URL 的 waypoints **並非無限制**，一般消費端網頁／App 實測最多穩定支援 **8 個**（不含起訖點），超過可能被忽略、截斷或連結開啟失敗。開發時實測確認，不要假設可塞入完整路徑。
- 引擎算出的 `path_coordinates` 可能有數十甚至上百點，**不能直接全塞進 waypoints**。

### 7.3 路徑簡化

用 **Douglas-Peucker 演算法**做線段簡化，保留代表路徑轉折形狀的關鍵點，簡化到 8 個中繼點（含起訖點共 10 點）以內。

⚠️ Google Maps 在相鄰兩中繼點之間仍用它自己的最快路徑演算法連接，所以簡化時應**優先保留「安全路徑明顯偏離最短路徑」的轉折點**（例如刻意繞開危險路口的那個轉彎），而非均勻取樣，否則簡化後路徑會又跑回 Google 的預設路線。實作上：先算出 α=0 的最短路徑，找出兩條路徑分歧最大的幾個點，強制保留。

### 7.4 UI 提醒

點擊「在 Google Maps 開啟」時，需提示使用者：Google Maps 的實際路線可能與推薦的安全路線略有出入。

---

## 8. 未定案事項

### 8.1 地點解析：Gemini 解析語意 + Google Places API 模糊搜尋（已定案）

使用者輸入是自由文字，Gemini 在上游（`gemini_chat_service.py` 的 function calling）先把它解析成明確的地點描述（`place_description`），但引擎需要的是座標，且模糊地點（連鎖店分館、口語地標）不能用純字串比對式的地址 Geocoding API 處理——這件事之前試過 Nominatim，準確度不夠；也試過完全交給 Gemini + Google Search Grounding 自己生座標，但 LLM 生成的數值不夠穩定可信賴。

現在改用 **Google Places API（New）的 Text Search**（`app/geocoding/google_places_geocoder.py`）：

- 把 `place_description` 丟給 Places API 的 `places:searchText`，交由專門的地點搜尋引擎做模糊比對，回傳一批候選地點（例如「新光三越」會回傳多間分店）。
- 有 `bias`（使用者目前位置）時會帶 `locationBias`（軟性偏向、非硬性篩選）避免分店數超過單頁上限（`pageSize=20`）的連鎖店擠不進候選清單。
- 「挑最近的候選」維持 deterministic：候選清單中實際離 `bias` 最近的一筆由後端用 haversine 公式**靜態計算**決定作為最終地點，不讓任何 LLM 或第三方 API 的排序結果直接當答案（呼應原則 2：安全與數值相關的判斷一律由後端 deterministic 程式碼計算）。
- 候選座標另有一層粗略的台灣經緯度範圍檢查，擋掉明顯搜尋錯國家的同名地點。
- 查無結果或請求失敗一律回傳 `None`，由呼叫方轉成 `GEOCODING_FAILED`（§6.5）。

結果一律做**覆蓋範圍檢查**：解析出的座標若不在路網範圍內，直接回 `OUT_OF_COVERAGE`。

### 8.2 展示範圍（阻塞所有人）

必須先確定是哪個城市／行政區、多大範圍。這決定 osmnx 抓多大的圖、要下載哪些政府資料集、以及路燈資料是否存在。

### 8.3 路燈資料可得性

若選定城市不開放路燈資料，`street_light` 類別留空，`lit_coverage_ratio` 回 `null`，以 `help_point` 密度作為照明 proxy，並在每次回覆顯示 warning。**不得默默把沒資料當成沒風險。**
