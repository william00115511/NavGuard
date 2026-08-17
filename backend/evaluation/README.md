# NavGuard vs Google Maps 安全性驗證（離線評估子專案）

證明「我們算出的路線比 Google Maps 更安全」不能只靠嘴巴說——這個子專案跑一批
隨機取樣的起訖點，讓我們的路線引擎與 Google Routes API 的實際導航路線，套用
**完全相同**的安全評分公式（`app/engine/safety.py` / `app/engine/metrics.py`，
未修改），產出可重現、可稽核的統計報告。

不掛進正式 FastAPI 服務，是獨立可執行的離線工具。

## 執行方式

```bash
cd backend
.venv/Scripts/python.exe -m evaluation.cli run --n 30 --seed 42
.venv/Scripts/python.exe -m evaluation.cli report
```

`run` 需要 `.env` 有 `MAPS_API_KEY`（見根目錄 `.env.example`）；Google API 回應會
快取到 `evaluation/output/google_cache/`，重跑 `report` 不需要再打 API。`report`
讀 `evaluation/output/results.csv`，產出：

- `evaluation/output/report.md`：統計摘要（勝率、平均安全分數差異與信賴區間、
  危險點位/照明/可求助據點/警局的平均差異、代價面）
- `evaluation/output/report.html`：同樣內容的自包含 HTML（inline SVG 圖表，可
  直接雙擊在瀏覽器打開，不需要額外套件或網路連線）
- `evaluation/output/failures.csv`：未納入比較的案例（Google 無路線／起訖點在
  路網上不連通），不悄悄從樣本中丟棄

## 方法論摘要

1. 起訖點從既有路網圖（`app/engine/graph.py`）隨機取樣（固定 seed 可重現），
   刻意不手動挑案例，避免選出對我們有利的組合。
2. 我們自己的「安全路線」「最快路線」直接呼叫 `LocalDataRouteEngine.calculate_route()`
   （未修改），跟正式 `/api/route/calculate` 走同一套引擎。
3. Google 路線是 Google Routes API（walking mode）解碼後的座標序列，用跟我們
   建路網 edge 完全相同的取樣方式（`sample_along`，每 25m 一個樣本）切成一串
   「偽 edge」（`evaluation/path_scoring.py`），再套用跟我們自己路線相同的
   `raw_edge_score` / `sigmoid_safety` / `build_metrics`。三條路線用同一把尺，
   比較才站得住腳。
4. 統計上用配對差異（同一組起訖點兩條路線相減）而非兩組獨立平均相減，並用
   bootstrap 給信賴區間，不是只丟一個看起來漂亮的平均值。
5. 一定同時揭露代價面（安全路線比最快路線多走幾分鐘），不能只講好處。

## 限制

- 安全分數是輔助決策依據，不是安全保證（呼應 `AGENTS.md` §1 原則 1）。
- 只反映本次資料集（路燈／可求助據點／危險點位／警局）與取樣範圍下的結果。
- 目前只評估靜態點位，不模擬動態事件（即時新聞回報的火警、人潮等）。
