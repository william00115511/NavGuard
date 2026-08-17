# 資料來源（AGENTS.md §3.1）

記錄 `backend/data/` 下每份本地檔案的來源、授權、覆蓋範圍與更新日期，作為回覆使用者時的資料透明度佐證。所有轉換腳本都在 `backend/scripts/`，執行期系統不會即時呼叫這些來源，資料要更新時由人手動重跑腳本覆蓋本地檔案。

## points/street_light.json

- **來源**：臺北市路燈位置分布圖（臺北市工務局公園路燈工程管理處，data.gov.tw）
- **URL**：https://tppkl.blob.core.windows.net/blobfs/TaipeiLight.csv
- **授權**：政府資料開放授權條款
- **覆蓋範圍**：大安區、信義區（`ingest_open_data.py --district 大安區 信義區`）
- **更新日期**：2026-08-17
- **腳本**：`scripts/ingest_open_data.py`

## points/police_station.json

- **來源**：Google Places API（New）Nearby Search，`includedTypes=["police"]`
- **授權**：Google Maps Platform 服務條款（需要有效的 `MAPS_API_KEY` 並啟用 Places API）
- **覆蓋範圍**：大安區、信義區
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`
- **備註**：每筆記錄的 `place_id` 是 `PointRecord` 的獨立欄位（不放在 `meta`）；`meta` 帶 Places 回傳的 `name`（displayName）、`address`（formattedAddress）、`phone`（nationalPhoneNumber，如有）。不再使用信心值折扣（`confidence` 沿用 schema 預設 1.0）。

## points/convenience_store.json

- **來源**：Google Places API（New）Nearby Search，`includedTypes=["convenience_store"]`，僅保留 `regularOpeningHours` 判定為全年無休 24 小時營業的分店
- **授權**：Google Maps Platform 服務條款（需要有效的 `MAPS_API_KEY` 並啟用 Places API）
- **覆蓋範圍**：大安區、信義區
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`
- **備註**：類別代號為 `convenience_store`（原 `help_point` 已停用）。每筆記錄的 `place_id` 是 `PointRecord` 的獨立欄位（不放在 `meta`）；`meta` 帶 Places 回傳的 `name`（displayName，已內含品牌／分店名稱，不再另外拆 `brand` 欄位）、`address`（formattedAddress）。此類別目前只涵蓋 24 小時超商，不含藥局／飯店大廳等其他可求助據點類型。不再使用信心值折扣（`confidence` 沿用 schema 預設 1.0）。

## points/danger_zone.json

- **來源**：
  1. **臺北市政府警察局婦幼安全警示地點**（臺北市政府警察局婦幼警察隊，data.taipei / data.gov.tw）
  2. **OpenStreetMap**（`amenity=nightclub,bar,pub` / `tunnel=yes`，Overpass API）
- **授權**：政府資料開放授權條款 / ODbL 1.0
- **覆蓋範圍**：信義區（涵蓋信義分局轄區官方公告之婦幼警示死角、地下道，以及松壽路/松智路周邊 25 家夜店酒吧醉漢聚集區）
- **類別登記**：`night_club_hazard`（夜店醉漢區）、`underpass_hazard`（封閉地下道）、`danger_zone`（治安警示死角）
- **更新日期**：2026-08-18
- **腳本**：`scripts/ingest_open_data.py`

## road_network.json

- **來源**：OpenStreetMap contributors（`highway=*` 步行路網，Overpass API）
- **授權**：ODbL 1.0
- **覆蓋範圍**：信義區（臺北市），16,971 個節點、19,925 條邊
- **更新日期**：2026-08-17
- **腳本**：`scripts/build_road_network.py`
- **備註**：AGENTS.md §3.5 的主要方案是用 `osmnx` 擷取存 GraphML／pickle；這裡改用專案已有的 `httpx` 直接打 Overpass API，換取同樣真實的路網拓樸但不新增地理空間依賴（networkx／shapely／geopandas）。node id 為 `osm_<OSM node id>`，重跑腳本更新路網後，若特定節點被 OSM 上游合併或刪除，其 id 可能改變。
