# 資料來源清單（AGENTS.md §3.1）

每一份進入計分的資料都必須能在這裡對應到來源、授權、覆蓋範圍與更新日期。
這份清單會直接回傳給使用者作為資料透明度佐證。

⚠️ **目前全部為 demo 佔位資料**，尚未接上真實政府開放資料集。展示前必須
把下表換成實際下載的資料，並更新「取得日期」與「覆蓋範圍」。

| 檔案 | 類別 | 來源 | 授權 | 覆蓋範圍 | 取得日期 |
|---|---|---|---|---|---|
| `points/street_light.json` | `street_light` | 示範資料（人工建立） | — | 台北車站～公館之間的示範網格 | 2026-08-17 |
| `points/police_station.json` | `police_station` | 示範資料（人工建立） | — | 同上 | 2026-08-17 |
| `points/help_point.json` | `help_point` | 示範資料（人工建立） | — | 同上 | 2026-08-17 |
| `points/danger_zone.json` | `danger_zone` | 示範資料（人工建立） | — | 同上 | 2026-08-17 |
| `road_network.json` | 路網 | 手動建立的簡化網格（§3.5 備援方案） | — | 同上 | 2026-08-17 |

## 待辦

- [ ] 確定展示行政區後，以 `osmnx`（`network_type="walk"`）擷取真實 OSM 路網
- [ ] 接上實際路燈開放資料；若該城市未開放，`street_light` 留空並依 §9.3
      以 `help_point` 密度作為照明 proxy，同時保留 warning
- [ ] 接上警政署派出所資料
- [ ] 犯罪資料一律以 grid／密度形式產出 `danger_zone`，不得包含精確歷史位置（§1 原則 5）

## 動態點位

Gemini 即時新聞搜尋產生的點位（`source_type: dynamic_realtime`）**不寫回本地檔案**，
只存在單次對話的記憶體中並附 `expires_at`，因此不列入本清單（§3.4）。
