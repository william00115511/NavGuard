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

- **來源**：各縣(市)警察(分)局暨所屬分駐(派出)所地址資料（內政部警政署，data.gov.tw）
- **URL**：https://www.tgos.tw/tgos/VirtualDir/Product/9927eb8a-efed-40c0-8bc4-83121ad6834a/1150729.zip
- **授權**：政府資料開放授權條款
- **覆蓋範圍**：大安區、信義區
- **更新日期**：2026-08-17
- **腳本**：`scripts/ingest_open_data.py`

## points/help_point.json

- **來源**：OpenStreetMap contributors（`shop=convenience`，Overpass API）
- **授權**：ODbL 1.0
- **覆蓋範圍**：大安區、信義區
- **更新日期**：2026-08-17
- **腳本**：`scripts/ingest_open_data.py`
- **備註**：Overpass 查詢行政區時必須用 `relation[...](area.city)` + `map_to_area` 過濾，不能寫成 `area(area.city)["name"="X"]`——後者不會真的按地理範圍過濾，同名行政區會全部混進來（例如「信義區」臺北市、基隆市都有，曾經把基隆信義區的店家也一起抓進來）。

## points/danger_zone.json

- **來源**：OpenStreetMap (OSM `amenity=nightclub,bar` / `tunnel=yes`) 與臺北市治安斑點示範資料
- **授權**：ODbL 1.0 / 政府資料開放授權條款
- **覆蓋範圍**：信義區（台北 101 至象山一帶，涵蓋松壽路夜店後巷、基隆路地下穿越道與信義路五段昏暗死角）
- **類別登記**：`night_club_hazard`（夜店醉漢區）、`underpass_hazard`（封閉地下道）、`danger_zone`（治安死角）
- **更新日期**：2026-08-18

## road_network.json

- **來源**：OpenStreetMap contributors（`highway=*` 步行路網，Overpass API）
- **授權**：ODbL 1.0
- **覆蓋範圍**：信義區（臺北市），16,971 個節點、19,925 條邊
- **更新日期**：2026-08-17
- **腳本**：`scripts/build_road_network.py`
- **備註**：AGENTS.md §3.5 的主要方案是用 `osmnx` 擷取存 GraphML／pickle；這裡改用專案已有的 `httpx` 直接打 Overpass API，換取同樣真實的路網拓樸但不新增地理空間依賴（networkx／shapely／geopandas）。node id 為 `osm_<OSM node id>`，重跑腳本更新路網後，若特定節點被 OSM 上游合併或刪除，其 id 可能改變。
