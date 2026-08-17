# 資料來源（AGENTS.md §3.1）

記錄 `backend/data/` 下每份本地檔案的來源、授權、覆蓋範圍與更新日期，作為回覆使用者時的資料透明度佐證。所有轉換腳本都在 `backend/scripts/`，執行期系統不會即時呼叫這些來源，資料要更新時由人手動重跑腳本覆蓋本地檔案。

## points/street_light.json

- **來源**：臺北市路燈位置分布圖（臺北市工務局公園路燈工程管理處，data.gov.tw）
- **URL**：https://tppkl.blob.core.windows.net/blobfs/TaipeiLight.csv
- **授權**：政府資料開放授權條款
- **覆蓋範圍**：信義區（`ingest_open_data.py --district 信義區`；對齊 `road_network.json` 目前只涵蓋信義區的路網，區外點位對路徑計算沒有任何作用，縮小範圍純粹是省下無謂的 Places API 查詢費用）
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`

## points/police_station.json

- **來源**：Google Places API（New）Nearby Search，`includedTypes=["police"]`
- **授權**：Google Maps Platform 服務條款（需要有效的 `MAPS_API_KEY` 並啟用 Places API）
- **覆蓋範圍**：信義區
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`
- **備註**：`place_id` 直接是 `PointRecord` 的唯一識別欄位（不再另外維護一個內部 `id`），值為 Google 回傳的真實 place_id；`meta` 帶 Places 回傳的 `name`（displayName）、`address`（formattedAddress）、`phone`（nationalPhoneNumber，如有）。不再使用信心值折扣（`confidence` 沿用 schema 預設 1.0）。

## points/convenience_store.json

- **來源**：Google Places API（New）Nearby Search，`includedTypes=["convenience_store"]`，僅保留 `regularOpeningHours` 判定為全年無休 24 小時營業的分店
- **授權**：Google Maps Platform 服務條款（需要有效的 `MAPS_API_KEY` 並啟用 Places API）
- **覆蓋範圍**：信義區
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`
- **備註**：類別代號為 `convenience_store`（原 `help_point` 已停用）。`place_id` 直接是 `PointRecord` 的唯一識別欄位，值為 Google 回傳的真實 place_id；`meta` 帶 Places 回傳的 `name`（displayName，已內含品牌／分店名稱，不再另外拆 `brand` 欄位）、`address`（formattedAddress）。此類別目前只涵蓋 24 小時超商，不含藥局／飯店大廳等其他可求助據點類型。不再使用信心值折扣（`confidence` 沿用 schema 預設 1.0）。

## points/danger_zone.json

- **來源**：
  1. **臺北市政府警察局婦幼安全警示地點**（臺北市政府警察局婦幼警察隊，data.taipei / data.gov.tw）
  2. **OpenStreetMap**（`amenity=nightclub,bar,pub` / `tunnel=yes`，Overpass API）
- **授權**：政府資料開放授權條款 / ODbL 1.0
- **覆蓋範圍**：信義區（涵蓋信義分局轄區官方公告之婦幼警示死角、地下道，以及松壽路/松智路周邊夜店酒吧醉漢聚集區）
- **類別登記**：`night_club_hazard`（夜店醉漢區）、`underpass_hazard`（封閉地下道）、`danger_zone`（治安警示死角）
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`
- **備註**：這裡是精確座標點，不是聚合後的 grid／density（§1 原則 5 字面上是為「受害者個資」類犯罪熱點資料設計的規則；這裡是政府/OSM 公開的地點類警示公告，不含個人資料，專案決定維持具體點位＋負面權重的呈現方式，見 AGENTS.md §4.6 修訂）。

## road_network.json

- **來源**：OpenStreetMap contributors（`highway=*` 步行路網，透過 `osmnx` 擷取）
- **授權**：ODbL 1.0
- **覆蓋範圍**：信義區（臺北市），24,019 個節點、27,850 條邊
- **更新日期**：2026-08-18
- **腳本**：`scripts/build_road_network.py`
- **備註**：改用 AGENTS.md §3.5 的主要方案 `osmnx`（`graph_from_bbox`，`network_type="walk"`，`simplify=False` 保留 way 形狀頂點）。輸出仍轉成跟舊版一致的精簡 `{nodes, edges}` JSON，`app/engine/graph.py` 完全不用改。node id 為 `osm_<OSM node id>`，重跑腳本更新路網後，若特定節點被 OSM 上游合併或刪除，其 id 可能改變。
