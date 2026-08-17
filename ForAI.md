# Safeway 規劃摘要（純對話式 + Gemini Function Calling 架構）

夜間步行安全導航。前端為對話框 UI，後端為 FastAPI + Gemini Function Calling，2 人分工。

## 1. 系統架構總覽

四層，各層間以明確資料格式溝通，任一層內部實作（換框架/演算法/模型）不影響其他層：

```
前端對話框(UI) —使用者訊息/顯示回覆與結果連結→
對話協調層(Backend + Gemini Function Calling，含即時新聞搜尋→動態點位回報) —呼叫 calculate_safe_route(origin, destination, priority)→
路徑計算引擎(讀本地路網+安全點位資料，建圖→加權→最短路徑演算法) —回傳路徑座標序列+統計摘要→
資料層 /data/points/*.json（靜態：路燈/警局/危險點位/路網，本地檔案永久；動態：Gemini即時新聞點位，session暫存，過期失效；兩者schema相同，可擴充，新增資料不需改程式碼）
           ↓
Google Maps URL 產生器（路徑簡化→組成中繼點→產生連結）
```

**關鍵原則**：資料層與運算層分離，運算層與對話層分離。Gemini 只負責「聽懂需求」與「觸發計算」，安全評分與路徑演算法完全由後端程式碼決定，不依賴 LLM 算數值（避免幻覺）。

---

## 2. 資料層設計（本地讀檔，可擴充）

### 2.1 資料取得方式
不在執行期即時call政府API。離線階段：從政府開放資料下載原始資料→轉換腳本標準化為統一點位格式→輸出本地檔案。執行期系統啟動時直接讀`/data/points/`本地檔案，不對外請求。轉換腳本與正式系統分開，之後更新資料重跑腳本覆蓋本地檔案即可。

### 2.2 統一點位資料格式
所有類型點位（路燈、警局、危險點位、監視器、即時新聞點位等）用同一schema：
```json
{
  "id": "streetlight_00123", "category": "street_light",
  "lat": 25.0478, "lng": 121.5319,
  "source": "台北市路燈資料_2026",
  "source_type": "static_local",
  "expires_at": null, "confidence": 1.0, "meta": {}
}
```
- `source_type`：`static_local`（本地離線資料）或 `dynamic_realtime`（2.5節Gemini即時搜尋加入）
- `expires_at`：靜態固定`null`（永久）；動態需ISO時間字串到期時間，過期後計算自動忽略
- `confidence`：靜態政府資料固定1.0；動態新聞來源可<1.0（如0.6），讓公式知道可信度較低、可調降影響力（見3.2節）

不同類別各自一檔案（`street_light.json`、`police_station.json`、`danger_zone.json`…），啟動時掃描整個目錄自動載入符合schema的檔案。**新增點位類型只需新增資料檔+在categories.json登記一筆設定，完全不用改程式邏輯**。

### 2.3 類別設定檔 `categories.json`（可擴充性關鍵）
定義每個類別對安全分數的影響方向（正面/負面）與影響半徑，運算層讀此設定計分，不寫死邏輯：
```json
{
  "street_light":   { "effect": "positive", "weight": 1.0, "radius_m": 30,  "kind": "static" },
  "police_station": { "effect": "positive", "weight": 3.0, "radius_m": 150, "kind": "static" },
  "danger_zone":    { "effect": "negative", "weight": 2.0, "radius_m": 80,  "kind": "static" },
  "fire_incident":  { "effect": "negative", "weight": 4.0, "radius_m": 200, "kind": "dynamic", "default_ttl_hours": 6 },
  "crowd_event":    { "effect": "positive", "weight": 1.5, "radius_m": 100, "kind": "dynamic", "default_ttl_hours": 12 }
}
```
未來加任何新類別，只要加一行設定+對應資料檔（或讓Gemini動態回報），程式碼完全不需修改。公式不認識具體類別名稱，只認識「正面/負面、影響半徑、權重」。

### 2.4 動態時效性點位（Gemini即時新聞搜尋）
1. Gemini對話中用網路搜尋能力（Grounding with Google Search）查路線附近「近期」相關新聞（火災、事故、管制區、臨時人潮等）
2. 找到後透過`report_dynamic_hazard`（見4.2b）把每則事件轉成點位回報後端：地點描述（後端轉座標）、類別、正負面、confidence、有效期限
3. 後端暫存於**這次對話**記憶體（不寫回本地靜態檔，避免未經查證新聞永久污染資料庫），與靜態點位合併交給第3節引擎計算
4. 動態點位一律附`expires_at`，計算時只採計未過期點位；對話結束或超過有效期自動失效

靜態與動態資料用完全相同schema與計分邏輯，差別只在生命週期與可信度。

### 2.5 路網資料（唯一尚未定案部分）
路徑計算需道路網路圖（節點=路口、邊=路段），政府路燈/警局資料無法提供，需另外準備：
- **建議方案**：OSM路網，針對展示範圍（如某行政區）用`osmnx`預先擷取存本地圖檔（GraphML/pickle/GeoJSON），放`/data/`目錄，執行期直接讀取
- **備援方案**（時間緊迫）：手動建簡化網格圖或幾條主要道路節點圖，僅供Demo，精確度較低但開發快

⚠️ 動工前需先確認展示範圍（哪個行政區/多大範圍），據此決定路網精細度。

---

## 3. 安全路徑計算引擎

### 3.1 整體流程
讀路網圖+所有點位資料 → 對每條edge算安全分數 → 距離+安全分數依優先程度合併成綜合成本 → 最短路徑演算法(Dijkstra/A*)在加權圖上找起訖點間成本最低路徑 → 回傳路徑座標序列+統計摘要（總距離、平均安全分數、經過幾個路燈/警局等）

### 3.2 Edge安全分數計算
對每條edge取中點（或每隔一定距離取樣），計算周圍點位加權影響：
```
edge_safety_score = Σ ( category.weight × decay(distance, category.radius_m) × sign × confidence )
```
- `sign`依`categories.json`的`effect`決定（positive=+1，negative=-1）——正面/負面點位在公式裡的唯一入口，新增任何點位類型只需決定+1或-1
- `decay()`：線性或高斯衰減，距離越近影響越大，超過`radius_m`趨近0
- `confidence`：靜態1.0；動態新聞點位可<1.0，讓不確定資訊影響力自動打折
- 計算前過濾掉已過期動態點位
- 分數正規化到0~1（1最安全）

### 3.3 綜合成本函數（安全 vs 速度）
用0~1的安全優先權重`α`（Gemini從對話萃取，或預設0.5）合併距離與安全分數：
```
edge_cost = (1 - α) × normalized_distance + α × (1 - normalized_safety_score)
```
- α=0：完全等同最快路徑；α=1：完全安全導向（可能繞遠路）；中間值為平衡
- α是Gemini可直接回傳的0~1浮點數，不侷限於「安全優先/速度優先」兩選項

### 3.4 演算法選擇
建議**A\***（有座標時用直線距離當heuristic，效率較Dijkstra好）；時間有限可用**Dijkstra**，正確性相同，黑客松規模路網通常足夠。

### 3.5 輸出格式
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
收集齊以下三項才觸發Function Calling，未齊全前持續追問：

| 欄位 | 說明 |
|---|---|
| origin | 起點文字描述，需轉座標（見4.4） |
| destination | 終點文字描述，需轉座標 |
| priority_alpha | 0~1安全優先權重，由「安全優先/速度優先/都可以」等語意推斷；「盡量安全」→給較高值 |

三項齊全後，可先做即時新聞搜尋、回報動態點位（4.2b），再呼叫`calculate_safe_route`。

### 4.2 Function Calling：`calculate_safe_route`
```json
{
  "name": "calculate_safe_route",
  "description": "根據起點、終點與安全優先程度，計算一條夜間步行安全路徑",
  "parameters": {
    "type": "object",
    "properties": {
      "origin": {"type": "string", "description": "起點地址或地標描述"},
      "destination": {"type": "string", "description": "終點地址或地標描述"},
      "priority_alpha": {"type": "number", "description": "0到1之間，0代表完全速度優先，1代表完全安全優先"}
    },
    "required": ["origin", "destination", "priority_alpha"]
  }
}
```
後端收到後執行第3節路徑引擎，結果（含`path_coordinates`）交回Gemini做總結回覆，同時交第5節URL產生器。

### 4.2b Function Calling：`report_dynamic_hazard`（對應2.4）
```json
{
  "name": "report_dynamic_hazard",
  "description": "回報一個從即時新聞/網路搜尋得知、具時效性的地點事件，會被納入本次路徑安全計算",
  "parameters": {
    "type": "object",
    "properties": {
      "location_description": {"type": "string", "description": "事件地點文字描述，後端會轉成座標"},
      "category": {"type": "string", "description": "事件類別，需對應categories.json中kind=dynamic的項目，例如fire_incident"},
      "effect": {"type": "string", "enum": ["positive", "negative"], "description": "對安全的正面或負面影響"},
      "confidence": {"type": "number", "description": "0~1，這則新聞/資訊的可信度"},
      "valid_hours": {"type": "number", "description": "此點位有效時數，未提供則使用該類別的default_ttl_hours"},
      "summary": {"type": "string", "description": "事件簡述，供回覆使用者時說明"}
    },
    "required": ["location_description", "category", "effect"]
  }
}
```
**流程順序**：Gemini收集完origin/destination後、呼叫`calculate_safe_route`前，先視需要搜尋並呼叫0至多次`report_dynamic_hazard`（每個事件一次），後端暫存於本次對話session；接著才呼叫`calculate_safe_route`，此時引擎自動合併靜態點位+本次session內尚未過期的動態點位一起計算。這代表Gemini需要支援即時網路搜尋工具（例如Grounding with Google Search），需確認所用Gemini版本/方案是否支援此功能與配額限制。

### 4.3 架構與安全性提醒
Gemini API呼叫**必須放在後端**，API Key不可放前端程式碼；前端只跟自己的後端溝通，由後端代理呼叫Gemini。

### 4.4 待確認：地址轉座標（Geocoding）
使用者輸入起訖點是文字（如「台北車站」），路徑引擎需要經緯度座標，中間需Geocoding步驟，**尚未指定服務**：
- 預算允許：Google Geocoding API（與最終輸出Google Maps一致性最好）
- 免費：OpenStreetMap Nominatim（有使用頻率限制）

### 4.5 前後端 API 介面設計
前端不需知道Gemini對話、Function Calling、路徑引擎、URL產生器的內部細節，只需跟後端「對話端點」互動；後端把整條流程包成黑箱，前後端可各自獨立開發測試。

#### 4.5.1 建立對話 Session
```
POST /api/session
```
用途：使用者打開對話框或按「開始新的路線規劃」時呼叫一次，取得`session_id`。
Request body：無（未來多使用者可傳`{ "user_id": "..." }`）
Response：
```json
{ "session_id": "sess_8f2a1c", "created_at": "2026-08-17T20:00:00+08:00" }
```
之後同一輪對話每則訊息都帶`session_id`，後端用它維護對話歷史與4.2b暫存的動態危險點位。

#### 4.5.2 傳送訊息／取得回覆（核心端點）
```
POST /api/chat
```
Request：
```json
{ "session_id": "sess_8f2a1c", "message": "我想從台北車站走到公館夜市，希望盡量安全" }
```
Response依進度分三種`status`：

**A. `collecting_info`**（資訊未齊全，Gemini繼續追問）
```json
{ "session_id": "sess_8f2a1c", "status": "collecting_info", "reply_text": "了解，你希望盡量安全對吧，那大概是想走多快到達呢？" }
```

**B. `route_ready`**（三項齊全，`calculate_safe_route`已執行完成）
```json
{
  "session_id": "sess_8f2a1c",
  "status": "route_ready",
  "reply_text": "幫你規劃了一條比較安全的路線，會經過1個派出所跟14盞路燈，距離約1.2公里。",
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
`dynamic_hazards_considered`：這次計算實際採計了哪些動態點位，前端可順便告訴使用者「路線有避開哪些即時事件」。

**C. `error`**（地點解析失敗、Gemini或路徑引擎發生問題）
```json
{ "session_id": "sess_8f2a1c", "status": "error", "error_code": "GEOCODING_FAILED", "reply_text": "抱歉，我找不到「公館夜市那邊」這個地點，可以講詳細一點的地標或地址嗎？" }
```

**前端邏輯**：不論哪個status，先把`reply_text`當Gemini訊息顯示在對話框；`route_ready`時額外顯示路線摘要卡片，並提供「在Google Maps開啟導航」按鈕連到`route.google_maps_url`。

#### 4.5.3 除錯用路徑計算端點
建議先獨立驗證路徑引擎、再接Gemini，因此額外開一支不經Gemini、直接呼叫路徑引擎的端點，方便前後端分開測試，也可保留作日後「進階模式」：
```
POST /api/route/calculate
```
Request：
```json
{ "origin": {"lat": 25.0478, "lng": 121.5319}, "destination": {"lat": 25.0170, "lng": 121.5340}, "priority_alpha": 0.7, "dynamic_hazards": [] }
```
Response：格式與`route`物件相同（含`google_maps_url`）。

#### 4.5.4 錯誤與狀態碼慣例
- 業務邏輯失敗（聽不懂地點、資訊不足等）一律回HTTP 200，body用`status: "error"`表示（請求本身有效，只是這次對話結果失敗）
- 只有系統層級錯誤用HTTP錯誤碼：`400`（request格式錯，如缺`message`）、`404`（`session_id`不存在）、`500`（後端內部錯誤，如Gemini API逾時、路徑引擎例外）
- 系統層級錯誤統一格式：
```json
{ "status": "error", "error_code": "SESSION_NOT_FOUND", "message": "..." }
```

> 補充：Gemini回覆（尤其牽涉即時搜尋）可能需數秒，MVP階段同步request/response即可；若等待感明顯可考慮把`/api/chat`改成SSE/WebSocket串流回覆，非必要加分項，不影響本節資料結構。

---

## 5. 路徑轉換為 Google Maps URL

### 5.1 URL格式
不需API Key的公開URL格式：
```
https://www.google.com/maps/dir/?api=1
  &origin={起點lat},{起點lng}
  &destination={終點lat},{終點lng}
  &waypoints={wp1_lat},{wp1_lng}|{wp2_lat},{wp2_lng}|...
  &travelmode=walking
```
`travelmode=walking`固定使用（夜間行人安全導航，非開車）。

### 5.2 ⚠️ 重要限制：中繼點數量
「用中繼點確保路徑相近」的關鍵風險：
- Google Maps URL中繼點**並非無限制**，一般消費端網頁/App實測穩定支援約**9~10個**，超過可能被忽略、截斷或連結開啟失敗，實際上限建議開發時實測確認，不要假設可塞入完整路徑所有節點
- 引擎算出的`path_coordinates`可能有數十甚至上百個座標點，**不能直接全塞進waypoints**，需先做路徑簡化

### 5.3 建議路徑簡化演算法
用**Douglas-Peucker（道格拉斯-普克）演算法**做線段簡化，保留能代表路徑轉折形狀的關鍵點、去除共線或幾乎共線的中間點，簡化到9個點以內（依實測上限調整）。

補充：Google Maps在相鄰兩中繼點之間仍用它自己的最快路徑演算法連接，所以簡化時應優先保留「安全路徑明顯偏離最快路徑」的轉折點（例如刻意繞開危險路口的那個轉彎），而非均勻取樣，否則簡化後路徑可能又跑回Google的預設最快路徑。

---

## 6. 兩人分工與解耦介面（Python ABC）

**分工方式**：
- **Dev A（API Router等）**：`/api/session`、`/api/chat`等HTTP路由、session管理、錯誤處理（4.5節）＋路徑計算引擎（第3節）＋本地資料讀取（第2節）＋Google Maps URL產生（第5節）
- **Dev B（Gemini Function Calling等）**：與Gemini API對話、slot filling（4.1節）、`calculate_safe_route`/`report_dynamic_hazard`兩個Function Calling工具（4.2、4.2b節）的觸發邏輯

兩人只透過下面兩個`abc.ABC`定義的介面溝通，任一方不需等對方寫完就能先用假實作（mock）開發測試。

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
    """對應第 2.4 / 4.2b 節：Gemini 即時搜尋到的時效性點位"""
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
        回報、尚未過期的動態點位（第 2.4 節）。
        """
        raise NotImplementedError
```

**平行開發方式**：
- Dev A先寫假的`FakeChatService(ChatService)`（固定回傳一則`route_ready`假資料），把API Router、session儲存、錯誤處理串起來測試，不等Dev B的Gemini邏輯完成
- Dev B先寫假的`FakeRouteEngine(RouteEngine)`（固定回傳假座標與假`google_maps_url`），專心把Gemini對話流程、slot filling、兩個Function Calling工具的觸發邏輯調通，不等Dev A的路徑演算法完成
- 兩邊做好後，把各自真實實作（`GeminiChatService`、`LocalDataRouteEngine`等）互相替換掉假物件即可整合，不需改動對方程式碼，因為溝通型別（`ChatResult`、`RouteResult`、`DynamicHazard`等）已先講好
