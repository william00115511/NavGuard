# Safeway — 夜間安全導航

> 為 24 小時 Dev Jam 的 Gemini API 賽道而設計。使用者輸入目的地後，系統不只回傳「最快路線」，而是產生數條可步行替代路線，依夜間照明、警察／便利商店等可求助據點、犯罪風險與繞路成本評分，顯示一條可說明原因的「較安全路線」。

## Demo 範圍與重要原則

- 目標場景：夜間步行；MVP 先選定一個城市／行政區，避免宣稱資料覆蓋全世界。
- 安全分數是**輔助決策，不是安全保證**。畫面要一直顯示「遇到緊急危險請撥當地緊急電話」，不得用「絕對安全」等文案。
- Google Maps Routes API 沒有「安全路線」參數，也不會提供完整路燈或犯罪資料；安全是本專案後端的可解釋評分模型，不能讓 Gemini 臆測。
- 現場最快可完成的版本：警察局、便利商店使用 Google Places；犯罪資料使用主辦方或政府公開的 GeoJSON/CSV；路燈資料若城市不開放，明確標為「未覆蓋」，改以夜間仍營業的可求助據點密度作為 proxy。

## 架構

```text
┌───────────────────────────────┐
│ Flutter Mobile App            │
│ - Google Map / GPS            │
│ - 搜尋目的地、畫路線與 marker │
└──────────────┬────────────────┘
               │ HTTPS  POST /v1/navigate
               ▼
┌──────────────────────────────────────────┐
│ FastAPI Backend · Cloud Run (safeway-api) │
│ - 路線候選、資料查詢、安全評分、Gemini agent │
└──────┬──────────────┬─────────────┬──────┘
       │              │             │
       ▼              ▼             ▼
┌─────────────┐ ┌─────────────┐ ┌──────────────────────────┐
│ Gemini API  │ │ Google Maps │ │ GeoJSON 靜態資料          │
│ 工具編排與  │ │ Routes API  │ │ - crime / streetlight     │
│ 繁中說明    │ │ Places API  │ │ - repo 或 Cloud Storage   │
└─────────────┘ └─────────────┘ └──────────────────────────┘
               │
               ▼
  回傳：polyline、分數、可求助據點、理由、資料警告
```

前端只保存畫面狀態，後端不保存使用者位置或歷史紀錄，因此目前**不需要資料庫**。後端可在啟動時將小型 GeoJSON 載入記憶體；資料較大時改放 Cloud Storage，以物件版本快取並定期重新載入。這仍不是資料庫。

## 技術選型

| 區塊 | 選擇 | 為何適合 24 小時黑客松 |
|---|---|---|
| Mobile | Flutter | 一套 Dart 程式直接跑 Android/iOS；可優先交付 Android APK。 |
| 地圖／定位 | `google_maps_flutter`、`geolocator` | 原生 Google 地圖、目前位置、polyline 與 marker 都成熟。 |
| API | Python 3.12 + FastAPI + Pydantic | 型別化 request/response，寫 agent tool 與評分邏輯很快。 |
| Agent | Gemini API + `google-genai` | Gemini 負責意圖理解、工具編排與把 deterministic 結果轉成自然語言。 |
| 地理服務 | Routes API、Places API (New) | 取可行走路線與真實 POI；API key 只留在後端。 |
| 部署 | Docker + Cloud Run | 前後端兩個獨立服務、無伺服器、自動擴縮。 |
| 秘密 | Secret Manager | Gemini 與 Maps key 不提交到 Git，也不放 Flutter。 |

## Repository 結構

```text
.
├── README.md
├── frontend/
│   ├── lib/
│   │   ├── main.dart
│   │   ├── screens/map_screen.dart
│   │   ├── services/safeway_api.dart
│   │   └── models/navigation_result.dart
│   ├── android/                 # Flutter 產生
│   ├── ios/                     # Flutter 產生
│   ├── web/                     # Cloud Run 網頁版所需
│   ├── Dockerfile
│   └── nginx.conf
└── backend/
    ├── app/
    │   ├── main.py              # FastAPI 路由、CORS、healthz
    │   ├── schemas.py            # Pydantic request/response
    │   ├── services/
    │   │   ├── routes.py         # Routes API client
    │   │   ├── places.py         # Places API client
    │   │   ├── safety_score.py   # 純函式、可測試的評分
    │   │   ├── datasets.py       # GeoJSON 載入與地理查詢
    │   │   └── gemini_agent.py   # Gemini tool orchestration
    │   └── data/demo_crime.geojson
    ├── tests/test_safety_score.py
    ├── requirements.txt
    └── Dockerfile
```

## 使用者流程

1. App 取得位置權限，將使用者座標、目的地文字或目的地座標與偏好送至 `POST /v1/navigate`。
2. Backend 用 Places Text Search 將目的地文字轉座標（若前端已選 Google Places autocomplete 結果，就直接傳 place ID／座標）。
3. Backend 呼叫 Routes API，要求 `WALK`、`computeAlternativeRoutes: true`，拿到最快路線及替代路線的 encoded polyline、距離、時間。
4. 對每條路線切成固定距離的 sampling points；在路線走廊（例如 50m）內查詢路燈、犯罪事件與 POI。
5. `safety_score.py` 以固定公式算分、列出可驗證 metrics。Gemini 只能讀這些結果、選擇使用者偏好與產生說明，不得改寫分數或製造資料。
6. 回傳最佳安全路線、最快路線與最多兩條替代方案；Flutter 畫線、marker、風險／安心點和可展開的理由。

## 後端 API 合約

### `POST /v1/navigate`

```json
{
  "origin": {"lat": 25.0330, "lng": 121.5654},
  "destination": {"text": "台北車站"},
  "mode": "WALK",
  "departure_time": "2026-08-17T22:00:00+08:00",
  "preferences": {
    "max_detour_minutes": 12,
    "prioritize_lighting": true,
    "prioritize_open_places": true
  }
}
```

```json
{
  "selected_route_id": "route-2",
  "disclaimer": "此建議依公開資料與即時營業資訊產生，無法保證安全；緊急狀況請立即求助。",
  "routes": [{
    "id": "route-2",
    "label": "推薦的較安全路線",
    "encoded_polyline": "...",
    "distance_meters": 1180,
    "duration_seconds": 1020,
    "safety_score": 78,
    "confidence": "medium",
    "metrics": {
      "lit_coverage_ratio": 0.71,
      "open_safe_places_within_50m": 5,
      "police_places_within_100m": 1,
      "crime_risk": 0.18,
      "data_coverage": ["poi", "crime"]
    },
    "reasons": ["沿途 5 個營業中可求助據點", "比最快路線多 4 分鐘，但犯罪風險較低"],
    "warnings": ["路燈資料在此區沒有覆蓋，未將照明納入評分"]
  }]
}
```

`GET /healthz` 回傳 `{"ok": true}`，供 Cloud Run health check 使用。錯誤使用一致格式：`400`（座標、偏好不合法）、`404`（找不到目的地）、`429`（上游配額）、`502`（上游 API 失敗）。

## 安全評分：可解釋、可替換資料

先將每個原始指標正規化為 `0..1`；缺資料絕不填 0，改為降低 `confidence` 並在 `warnings` 顯示。建議 MVP 公開公式：

```text
score = 100 × clamp(
  0.30 × light_coverage
  + 0.25 × nearby_open_help_points
  + 0.15 × police_proximity
  + 0.20 × (1 - crime_risk)
  + 0.10 × detour_acceptability,
  0, 1
)
```

- `light_coverage`：路線採樣點中，50m 內有「確認的路燈／照明道路」資料的比例。MVP 沒資料就移除該權重，再把其餘權重重新正規化。
- `nearby_open_help_points`：在預計通過時間仍營業的便利商店、24h 藥局、飯店大廳等；Places 只查這些可明確求助的類型。
- `police_proximity`：路線與警察局的最短距離／沿線數量；它是輔助指標，不表示有即時警力。
- `crime_risk`：依最近 6–12 個月、路線走廊內的事件密度與嚴重性加權；資料的日期、來源、覆蓋範圍要隨 response 回傳。
- `detour_acceptability`：和最快路線相比的額外分鐘數，超過使用者允許值即大幅扣分或不推薦。

**資料收集建議**：賽前先把一個示範區的開放資料轉為 `FeatureCollection`。Crime feature 至少有 `occurred_at`、`severity`、`geometry`；light feature 至少有 `geometry` 與 `verified_at`。建立 `data_sources.md` 記錄 URL、授權、更新日期。不要收集受害者個資；不要將精確歷史犯罪位置直接畫在使用者地圖上，以 grid／密度呈現。

## Gemini agent 設計

Gemini 不應是安全分數的來源；它是有界限的 agent。使用 `google-genai` 的 function calling 或 structured output，僅提供以下工具：

```text
resolve_destination(query)       -> {lat, lng, display_name}
get_walking_routes(origin, dest) -> [{polyline, distance, duration}]
inspect_route_safety(route)      -> {metrics, score, warnings}
compose_route_explanation(data)  -> {title, reasons, warnings}
```

System instruction：

```text
你是 Safeway 的夜間導航說明助手。你必須呼叫工具取得地理資料；
不得編造路燈、營業時間、警方位置、犯罪統計或安全保證。
資料缺漏時，要在 warnings 清楚說明。若使用者表示正遭遇危險，優先
建議聯絡當地緊急服務、前往最近明亮且有人員的公共場所，停止一般導航。
```

在正式 API response 使用 Pydantic schema／Gemini structured output 驗證；tool response 仍由 `safety_score.py` 決定數值。Hackathon demo 可採 `gemini-2.5-flash`（在專案啟動時以環境變數 `GEMINI_MODEL` 指定），以便日後換模型而不改程式。

## 外部 API 實作注意事項

### Routes API

後端向 `https://routes.googleapis.com/directions/v2:computeRoutes` 發出 POST。關鍵 headers 為：

```http
X-Goog-Api-Key: $GOOGLE_MAPS_SERVER_KEY
X-Goog-FieldMask: routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline,routes.legs.steps
Content-Type: application/json
```

body 指定 `travelMode: "WALK"` 和 `computeAlternativeRoutes: true`。若 API 只給一條替代路線，MVP 可以接受；不要靠假造不同路線來充數。下一階段可對經過不同 waypoint 的多次 `computeRoutes` 呼叫產生候選，但要設上限並快取，以控制費用與延遲。

### Places API (New)

以 Text Search 找目的地和沿線 POI。先用 polyline 的採樣點分批查詢，限制半徑、最多結果與 POI 類別；然後在後端去重。只索取需要欄位，例如 `places.id,places.displayName,places.location,places.regularOpeningHours,places.currentOpeningHours,places.types`，因為 Places field mask 影響費用與回傳大小。當營業資料缺失，標成 unknown，不要當作 open。

### Flutter 金鑰分離

- Mobile 的 Maps SDK key 可存在 Android `AndroidManifest.xml`／iOS `AppDelegate` 設定，但必須限制為 Android package + SHA-1、iOS bundle ID。
- `GOOGLE_MAPS_SERVER_KEY`（Routes／Places）與 `GEMINI_API_KEY` **只能在 backend**，交給 Secret Manager 注入 Cloud Run。
- production backend 啟用 CORS allowlist（Flutter mobile 本身不受 browser CORS 影響，Flutter web 才需要），加上 rate limit、request payload 上限與 Cloud Logging 的敏感欄位遮罩。

## 本機啟動

### 1. 建立專案骨架

```bash
flutter create frontend
mkdir -p backend/app/services backend/app/data backend/tests
```

Flutter 加入：`google_maps_flutter`、`geolocator`、`dio`（或 `http`）、`flutter_polyline_points`。Python 加入：`fastapi`、`uvicorn[standard]`、`httpx`、`pydantic-settings`、`google-genai`、`shapely`（或先以 Haversine 距離做 MVP）。

### 2. Backend 環境變數

```bash
export GEMINI_API_KEY='...'
export GOOGLE_MAPS_SERVER_KEY='...'
export ALLOWED_ORIGINS='http://localhost:3000'
export GEMINI_MODEL='gemini-2.5-flash'
cd backend
uvicorn app.main:app --reload --port 8080
```

### 3. Flutter

```bash
cd frontend
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8080
```

Android emulator 用 `10.0.2.2` 連本機；實體手機請改用同網段電腦 IP 的 HTTPS tunnel 或已部署的 Cloud Run URL。用 `--dart-define` 傳 public API URL，永遠不要傳 server API key。

## Cloud Run 部署

部署兩個獨立的 Cloud Run service：`safeway-api` 與 `safeway-web`。Mobile app 是直接向 `safeway-api` 發 API request，不需要上架前端 server；`safeway-web` 只是 demo 網頁版／評審掃 QR code 的入口。

### 一次性 GCP 設定

```bash
gcloud config set project PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com routes.googleapis.com places.googleapis.com

printf %s 'YOUR_GEMINI_KEY' | gcloud secrets create gemini-api-key --data-file=-
printf %s 'YOUR_MAPS_SERVER_KEY' | gcloud secrets create maps-server-key --data-file=-
```

若採 Gemini Developer API，`GEMINI_API_KEY` 即可；若改用 Vertex AI，再啟用 `aiplatform.googleapis.com`、移除 key、以 Cloud Run service account 授權 `roles/aiplatform.user`，並改用 Vertex AI 的 Gemini client 設定。兩種方式擇一，避免混用。Flutter 地圖則在 Google Cloud Console 另外啟用 Maps SDK for Android／iOS，並對各自的 mobile key 設定 application restriction。

### Backend Dockerfile

`backend/Dockerfile`：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

部署：

```bash
gcloud run deploy safeway-api --source backend --region asia-east1 \
  --allow-unauthenticated --port 8080 --memory 1Gi --timeout 60 \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,GOOGLE_MAPS_SERVER_KEY=maps-server-key:latest \
  --set-env-vars GEMINI_MODEL=gemini-2.5-flash,ALLOWED_ORIGINS=https://YOUR_WEB_DOMAIN
```

Cloud Run 會注入 `PORT`，container 必須在 `0.0.0.0:$PORT` 監聽。先公開 API 是黑客松方便的折衷；至少以 API gateway／rate limit、Maps key API 限制與配額保護。正式服務則讓 API require authentication，並以 Firebase Authentication 驗證 mobile 使用者 token。

### Frontend web Dockerfile（可選）

先在 build time 寫入公開 API URL：

```dockerfile
FROM ghcr.io/cirruslabs/flutter:stable AS build
WORKDIR /src
COPY . .
ARG API_BASE_URL
RUN flutter pub get && flutter build web --release --dart-define=API_BASE_URL=$API_BASE_URL
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/build/web /usr/share/nginx/html
```

```bash
gcloud builds submit frontend --tag asia-east1-docker.pkg.dev/PROJECT_ID/safeway/safeway-web:latest
gcloud run deploy safeway-web --image asia-east1-docker.pkg.dev/PROJECT_ID/safeway/safeway-web:latest \
  --region asia-east1 --allow-unauthenticated
```

`API_BASE_URL` 是公開資訊，不要放秘密；若 Cloud Build 需傳 build arg，使用 `cloudbuild.yaml` 或先由 CI 注入。最省時的評審 demo 路線是直接 `flutter build apk --release` 安裝 APK，web service 作為備用。

## 24 小時執行順序

| 時間 | 可交付成果 |
|---|---|
| 0–3h | Flutter 地圖、目前位置、目的地輸入；FastAPI `/healthz`、mock `/navigate`。 |
| 3–7h | 接 Routes API，讓最快步行路線畫在地圖上。 |
| 7–11h | 接 Places，將警察局／營業中超商畫 marker；做 deterministic 評分與兩條候選路線。 |
| 11–14h | 加入一個示範行政區 Crime GeoJSON；資料缺漏 warning、單元測試。 |
| 14–17h | Gemini agent 把工具結果轉成繁中理由與問答；保留工具呼叫 log。 |
| 17–20h | Docker、Cloud Run、Secret Manager、真機測試。 |
| 20–24h | 做 loading／error state、錄 demo、準備資料來源與限制說明。 |

## 評審 Demo 劇本

1. 在晚間模式地圖選擇一個目的地，先顯示最快路線（例如 14 分鐘）。
2. 點「尋找較安全路線」：畫出 18 分鐘的推薦線，卡片列出「5 個營業中求助據點」、「經過警察局 1 處」、「公開犯罪資料風險較低」。
3. 點任一 marker 顯示名稱、距離、營業資料來源；點風險資訊顯示 crime dataset 最後更新日與範圍。
4. 關掉路燈資料時，刻意顯示 warning，證明系統不會把未知說成安全。
5. 請 Gemini 用一句話解釋取捨，例如「多走 4 分鐘以避開較高風險區段，並增加可求助據點」。

## 驗收清單

- [ ] 不把 server key 或 Gemini key 放進 Git／APK。
- [ ] 任一 source response 可連到公開資料來源、日期和覆蓋區域。
- [ ] 每條推薦均顯示距離、時間、分數、資料信心與 warning。
- [ ] 「最快」與「較安全」可以並列比較，使用者可手動選擇。
- [ ] `/healthz`、目的地找不到、上游配額與定位拒絕都有可理解畫面。
- [ ] Cloud Run logs 不記錄精確 origin/destination 或 API keys。
- [ ] 以固定 GeoJSON fixture 針對評分函式做單元測試，確保 crime 增加會降分、資料缺失會降 confidence。

## 官方參考

- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output) 與 [function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Routes API `computeRoutes`](https://developers.google.com/maps/documentation/routes/compute_route_directions)
- [Places API (New) Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Cloud Run Python/FastAPI 部署](https://cloud.google.com/run/docs/quickstarts/build-and-deploy/deploy-python-fastapi-service)
- [Cloud Run container runtime contract](https://cloud.google.com/run/docs/container-contract)
- [Flutter installation and build](https://docs.flutter.dev/install)
