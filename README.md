# NavGuard 夜間步行安全導航

使用者透過對話說出「我想從 A 走到 B，希望安全一點」，系統在自建的本地路網圖上，以夜間照明、可求助據點、危險點位加權計算，回傳一條可解釋原因的較安全步行路線。

後端 FastAPI + Gemini Function Calling，前端 Flutter。詳細設計與 API 合約見 [AGENTS.md](AGENTS.md)。

## 專案結構

```text
backend/    FastAPI 後端：對話協調、安全路徑計算引擎、資料層
frontend/   Flutter 前端（Android／iOS／Web）
```

## 快速開始

### 後端

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate       # Windows；macOS/Linux 用 source .venv/bin/activate
pip install -r requirements.txt
```

複製根目錄的 `.env.example` 為 `.env` 並依需求填入設定（Vertex AI 憑證、Google Maps API Key 等）：

```bash
cp .env.example .env
```

啟動開發伺服器：

```bash
uvicorn app.main:app --reload --port 8000
```

啟動後可用 `GET /healthz` 確認路網與點位資料是否載入成功。

### 前端

```bash
cd frontend
flutter pub get
flutter run
```

## 測試

```bash
cd backend
pytest
```

## 環境變數

見 [.env.example](.env.example)：

| 變數 | 說明 |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Vertex AI service account 憑證檔路徑 |
| `VERTEX_LOCATION` | Vertex AI 區域 |
| `MAPS_API_KEY` | Google Maps／Places API Key（僅後端使用） |
| `CHAT_SERVICE_BACKEND` | 對話服務實作（如 `fake`／正式 Gemini） |
| `GEMINI_MODEL` | 使用的 Gemini 模型 |
| `SESSION_TTL_SECONDS` | Session 閒置過期秒數 |
| `MAX_HISTORY_MESSAGES` | 對話歷史保留訊息數上限 |

## 資料準備

路網圖與點位資料為離線腳本產出，不在執行期即時呼叫外部 API：

```bash
cd backend
python scripts/build_road_network.py   # 產生 data/road_network.json
python scripts/ingest_open_data.py     # 轉換政府開放資料為統一點位格式
```

## 部署

後端提供 `Dockerfile`，可部署至 Cloud Run（依 `PORT` 環境變數動態監聽）。
