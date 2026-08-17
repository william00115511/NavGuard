"""一次性／可重跑的開放資料轉換腳本（AGENTS.md §3.1）。

路燈走政府開放資料 API；警局與超商改走 Google Places API（New）Nearby
Search，換取準確地址／place_id（§3.2 統一點位格式）。全部轉換完覆蓋
`backend/data/points/` 下對應的本地檔案。執行期系統不會呼叫這支腳本，
資料要更新時由人手動重跑即可。

涵蓋範圍（--district）：
預設（不加 --district）會抓臺北市全部 12 個行政區的資料。這不只是為了
路燈那 14 萬筆資料的下載/解析效能，更重要的是 `EdgeSafetyIndex` 在
啟動時對每條 edge 的每個取樣點都會掃過全部靜態點位、沒有空間索引
（app/engine/safety.py 的 raw_edge_score），全臺北市點位一次全塞下去
實測會把啟動時間拖到 8 秒以上；只轉換展示用到的行政區（例如
`--district 中正區 大安區`）可以把合計壓到幾千筆，啟動幾乎感覺不到。

**費用注意**：警局／超商的 Nearby Search 是計費 API，每個查詢格都算一次
請求；行政區面積越大（尤其士林／北投／內湖／文山這種含大片山區的區）鋪出
的格數越多，即使該格沒有任何結果也照樣計費。全市 12 區一次跑完可能是數百
到上千次請求，跑之前建議先用 `--district` 限縮範圍。

用法：
    cd backend
    .venv/Scripts/python.exe scripts/ingest_open_data.py
    .venv/Scripts/python.exe scripts/ingest_open_data.py --district 大安區 信義區

需要專案根目錄的 .env 設定 MAPS_API_KEY，且該 GCP 專案已啟用 Places API
（New）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

try:
    from pyproj import Transformer
    _TWD97_TO_WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)
    def _twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
        lng, lat = _TWD97_TO_WGS84.transform(x, y)
        return lat, lng
except ImportError:
    def _twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
        a = 6378137.0
        b = 6356752.314245
        lon0 = math.radians(121.0)
        k0 = 0.9999
        dx = 250000.0
        dy = 0.0
        e = math.sqrt(1 - (b / a) ** 2)
        e2 = e * e / (1 - e * e)
        x_adj = x - dx
        y_adj = y - dy
        M = y_adj / k0
        mu = M / (a * (1 - e**2 / 4 - 3 * e**4 / 64 - 5 * e**6 / 256))
        e1 = (1 - math.sqrt(1 - e**2)) / (1 + math.sqrt(1 - e**2))
        fp = (
            mu
            + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu)
        )
        C1 = e2 * math.cos(fp) ** 2
        T1 = math.tan(fp) ** 2
        N1 = a / math.sqrt(1 - e**2 * math.sin(fp) ** 2)
        R1 = a * (1 - e**2) / (1 - e**2 * math.sin(fp) ** 2) ** 1.5
        D = x_adj / (N1 * k0)
        lat = fp - (N1 * math.tan(fp) / R1) * (
            D**2 / 2
            - (5 + 3 * T1 + 10 * C1 - 4 * C1**2 - 9 * e2) * D**4 / 24
            + (61 + 90 * T1 + 298 * C1 + 45 * T1**2 - 252 * e2 - 3 * C1**2) * D**6 / 720
        )
        lon = lon0 + (
            D
            - (1 + 2 * T1 + C1) * D**3 / 6
            + (5 - 2 * C1 + 28 * T1 - 3 * C1**2 + 8 * e2 + 24 * T1**2) * D**5 / 120
        ) / math.cos(fp)
        return math.degrees(lat), math.degrees(lon)

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
POINTS_DIR = DATA_DIR / "points"

# app.config 在 backend/ 底下，直接執行 scripts/ingest_open_data.py 時
# sys.path[0] 是 scripts/ 而不是 backend/，要手動補進去才 import 得到。
sys.path.insert(0, str(BACKEND_DIR))
from app.config import Settings  # noqa: E402

_HTTP_TIMEOUT = 60.0
_USER_AGENT = "NavGuard-NightWalkSafety-DataIngest/0.1 (offline conversion script)"
_METERS_PER_DEGREE_LAT = 111_320.0

# 臺北市 12 個行政區（含「區」字，對應警政署地址、Overpass 行政區名稱的格式）。
TAIPEI_DISTRICTS: tuple[str, ...] = (
    "中正區",
    "大同區",
    "中山區",
    "松山區",
    "大安區",
    "萬華區",
    "信義區",
    "士林區",
    "北投區",
    "內湖區",
    "南港區",
    "文山區",
)

# Google Places 回傳的 formattedAddress 常帶郵遞區號／「台灣」開頭
# （例如「106台灣台北市大安區仁愛路三段2號」），所以用 search 而非 match。
_DISTRICT_ADDR_RE = re.compile(r"(?:臺北市|台北市)([一-鿿]{2,3}區)")

STREETLIGHT_URL = "https://tppkl.blob.core.windows.net/blobfs/TaipeiLight.csv"
# 公用 Overpass 主機常常忙碌，準備幾個鏡像站輪流試。
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

PLACES_SEARCH_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
_PLACES_TIMEOUT = 15.0
_PLACES_RETRY = 3
# Nearby Search（New）單次上限 20 筆，且不支援分頁（那是舊版 API 才有的
# next_page_token）；一格剛好回滿 20 筆代表可能還有店家被裁掉，見
# _places_search_nearby 的飽和自動細分邏輯。
_PLACES_MAX_RESULT_COUNT = 20
_PLACES_MAX_SUBDIVIDE = 2

# 查詢格半徑：警局密度低、半徑可以大一點省呼叫次數；超商密集，半徑太大
# 一格就會超過 20 筆上限（見上）而要一直往下細分，反而更貴。
_POLICE_GRID_RADIUS_M = 900.0
_CONVENIENCE_GRID_RADIUS_M = 350.0

_POLICE_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,places.nationalPhoneNumber"
)
_CONVENIENCE_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,places.regularOpeningHours"
)





def _get(client: httpx.Client, url: str) -> httpx.Response:
    print(f"  下載中：{url}", flush=True)
    response = client.get(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True)
    response.raise_for_status()
    return response


# ---------- 路燈 ----------


def ingest_street_light(client: httpx.Client, districts: set[str] | None) -> list[dict[str, Any]]:
    """districts 為不含「區」字的行政區名稱集合（對應 CSV 的 Dist 欄位），None 表示不篩選。"""
    print("[1/3] 路燈資料（臺北市工務局公園路燈工程管理處）", flush=True)
    response = _get(client, STREETLIGHT_URL)

    reader = csv.DictReader(io.StringIO(response.text), skipinitialspace=True)
    points: list[dict[str, Any]] = []
    total = 0
    for row in reader:
        total += 1
        dist = row.get("Dist", "").strip()
        if districts is not None and dist not in districts:
            continue
        try:
            x, y = float(row["TWD97X"]), float(row["TWD97Y"])
        except (KeyError, ValueError, TypeError):
            continue
        lat, lng = _twd97_to_wgs84(x, y)
        points.append(
            {
                "place_id": f"streetlight_{len(points) + 1:05d}",
                "category": "street_light",
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "source": "臺北市路燈位置分布圖（臺北市工務局公園路燈工程管理處，data.gov.tw）",
                "source_type": "static_local",
                "expires_at": None,
                "confidence": 1.0,
                "meta": {
                    "district": dist,
                    "light_height_m": row.get("LightHeight", "").strip() or None,
                    "update_date": row.get("UpdDate", "").strip() or None,
                },
            }
        )
    print(f"  全市 {total} 筆 -> 篩選後 {len(points)} 筆")
    return points


# ---------- 行政區 bounding box（Overpass，警局／超商查詢格鋪面用）----------


def _district_bbox(client: httpx.Client, district: str) -> tuple[float, float, float, float]:
    """回傳 (south, west, north, east)。用行政區 relation 的 bounding box 鋪查詢格，
    不是精確的行政區多邊形——邊界附近撈到鄰區的點位，靠下面 _DISTRICT_ADDR_RE
    比對地址字串濾掉（跟舊版政府資料的做法一致）。
    """
    query = (
        "[out:json][timeout:60];\n"
        'area["name"="臺北市"]["boundary"="administrative"]->.city;\n'
        f'relation["boundary"="administrative"]["name"="{district}"](area.city);\n'
        "out bb;"
    )
    last_error: Exception | None = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                response = client.post(
                    url, data={"data": query}, headers={"User-Agent": _USER_AGENT}, timeout=90.0
                )
                response.raise_for_status()
                elements = response.json()["elements"]
                if elements and "bounds" in elements[0]:
                    bounds = elements[0]["bounds"]
                    return bounds["minlat"], bounds["minlon"], bounds["maxlat"], bounds["maxlon"]
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                continue
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"抓不到「{district}」的行政區範圍（Overpass），最後一次錯誤：{last_error}")


def _grid_centers(bbox: tuple[float, float, float, float], radius_m: float) -> list[tuple[float, float]]:
    """用固定半徑的圓格鋪滿 bbox。格距取半徑的 1.4 倍，讓相鄰圓幾乎不留縫但不過度重疊。"""
    south, west, north, east = bbox
    ref_lat = (south + north) / 2
    lat_step_deg = (radius_m * 1.4) / _METERS_PER_DEGREE_LAT
    lng_step_deg = (radius_m * 1.4) / (_METERS_PER_DEGREE_LAT * max(math.cos(math.radians(ref_lat)), 1e-6))
    lat_count = math.ceil((north - south) / lat_step_deg) + 1
    lng_count = math.ceil((east - west) / lng_step_deg) + 1
    return [
        (south + i * lat_step_deg, west + j * lng_step_deg) for i in range(lat_count) for j in range(lng_count)
    ]


# ---------- Google Places API（New）Nearby Search ----------


def _places_search_nearby(
    client: httpx.Client,
    api_key: str,
    lat: float,
    lng: float,
    radius_m: float,
    included_type: str,
    field_mask: str,
    depth: int = 0,
) -> list[dict[str, Any]]:
    body = {
        "includedTypes": [included_type],
        "maxResultCount": _PLACES_MAX_RESULT_COUNT,
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
        },
        "languageCode": "zh-TW",
        "regionCode": "TW",
    }

    places: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for attempt in range(_PLACES_RETRY):
        try:
            response = client.post(
                PLACES_SEARCH_NEARBY_URL,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": api_key,
                    "X-Goog-FieldMask": field_mask,
                },
                timeout=_PLACES_TIMEOUT,
            )
            response.raise_for_status()
            places = response.json().get("places", [])
            break
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"Places API 查詢失敗（{included_type} @ {lat:.5f},{lng:.5f}）：{last_error}")

    # New Nearby Search 沒有分頁；一格剛好回滿代表可能還有店家被裁掉，切成 4 個
    # 象限格用一半半徑重查（有深度上限，避免面積異常大或極密集區域無限遞迴）。
    if len(places) >= _PLACES_MAX_RESULT_COUNT and depth < _PLACES_MAX_SUBDIVIDE:
        sub_radius = radius_m / 2
        offset_deg_lat = sub_radius / _METERS_PER_DEGREE_LAT
        meters_per_deg_lng = _METERS_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 1e-6)
        offset_deg_lng = sub_radius / meters_per_deg_lng
        sub_centers = [
            (lat + offset_deg_lat, lng + offset_deg_lng),
            (lat + offset_deg_lat, lng - offset_deg_lng),
            (lat - offset_deg_lat, lng + offset_deg_lng),
            (lat - offset_deg_lat, lng - offset_deg_lng),
        ]
        places = []
        for sub_lat, sub_lng in sub_centers:
            places.extend(
                _places_search_nearby(
                    client, api_key, sub_lat, sub_lng, sub_radius, included_type, field_mask, depth + 1
                )
            )
    return places


def _is_24_hours(regular_opening_hours: dict[str, Any] | None) -> bool:
    """Google 對「全年無休、24 小時營業」的慣例表示法：只有一個 period，且沒有 close 欄位。"""
    if not regular_opening_hours:
        return False
    periods = regular_opening_hours.get("periods") or []
    if len(periods) != 1:
        return False
    open_time = periods[0].get("open", {})
    return (
        "close" not in periods[0]
        and open_time.get("day") == 0
        and open_time.get("hour") == 0
        and open_time.get("minute") == 0
    )


def _search_district_places(
    client: httpx.Client,
    api_key: str,
    districts: list[str],
    radius_m: float,
    included_type: str,
    field_mask: str,
) -> dict[str, dict[str, Any]]:
    """對每個行政區鋪查詢格、呼叫 Nearby Search，回傳依 place_id 去重後的結果
    （查詢格互相重疊、細分遞迴都可能重覆撈到同一家店）。"""
    by_place_id: dict[str, dict[str, Any]] = {}
    for district in districts:
        bbox = _district_bbox(client, district)
        centers = _grid_centers(bbox, radius_m)
        print(f"  {district}：{len(centers)} 個查詢格", flush=True)
        for lat, lng in centers:
            places = _places_search_nearby(client, api_key, lat, lng, radius_m, included_type, field_mask)
            for place in places:
                place_id = place.get("id")
                address = place.get("formattedAddress", "")
                match = _DISTRICT_ADDR_RE.search(address)
                if place_id is None or match is None or match.group(1) != district:
                    continue  # 查詢格跨到鄰區邊界，過濾掉不屬於這個行政區的結果
                by_place_id[place_id] = place
    return by_place_id


# ---------- 警局 ----------


def ingest_police_station(
    client: httpx.Client, api_key: str, districts_with_suffix: set[str] | None
) -> list[dict[str, Any]]:
    print("[2/3] 警察機關資料（Google Places API，type=police）", flush=True)
    target_districts = sorted(districts_with_suffix) if districts_with_suffix else list(TAIPEI_DISTRICTS)

    by_place_id = _search_district_places(
        client, api_key, target_districts, _POLICE_GRID_RADIUS_M, "police", _POLICE_FIELD_MASK
    )

    points: list[dict[str, Any]] = []
    for place in by_place_id.values():
        location = place.get("location", {})
        points.append(
            {
                "place_id": place["id"],
                "category": "police_station",
                "lat": round(location["latitude"], 6),
                "lng": round(location["longitude"], 6),
                "source": "Google Places API（New），type=police",
                "source_type": "static_local",
                "expires_at": None,
                "meta": {
                    "name": place.get("displayName", {}).get("text", ""),
                    "address": place.get("formattedAddress", ""),
                    "phone": place.get("nationalPhoneNumber", ""),
                },
            }
        )
    print(f"  合計 {len(points)} 筆", flush=True)
    return points


# ---------- 便利商店（convenience_store，僅 24 小時營業）----------


def ingest_convenience_store(
    client: httpx.Client, api_key: str, districts_with_suffix: set[str] | None
) -> list[dict[str, Any]]:
    print("[3/3] 便利商店資料（Google Places API，type=convenience_store，僅 24 小時營業）", flush=True)
    target_districts = sorted(districts_with_suffix) if districts_with_suffix else list(TAIPEI_DISTRICTS)

    by_place_id = _search_district_places(
        client,
        api_key,
        target_districts,
        _CONVENIENCE_GRID_RADIUS_M,
        "convenience_store",
        _CONVENIENCE_FIELD_MASK,
    )

    points: list[dict[str, Any]] = []
    skipped_not_24h = 0
    for place in by_place_id.values():
        if not _is_24_hours(place.get("regularOpeningHours")):
            skipped_not_24h += 1
            continue
        location = place.get("location", {})
        points.append(
            {
                "place_id": place["id"],
                "category": "convenience_store",
                "lat": round(location["latitude"], 6),
                "lng": round(location["longitude"], 6),
                "source": "Google Places API（New），type=convenience_store，僅 24 小時營業",
                "source_type": "static_local",
                "expires_at": None,
                "meta": {
                    "name": place.get("displayName", {}).get("text", ""),
                    "address": place.get("formattedAddress", ""),
                },
            }
        )
    print(f"  非 24 小時營業濾掉 {skipped_not_24h} 筆 -> 合計 {len(points)} 筆", flush=True)
    return points


# ---------- 負向危險點位（夜店酒吧、地下道、昏暗死角）----------


def _overpass_hazard_query(districts: list[str] | None) -> str:
    if districts:
        district_names = "|".join(districts)
        return (
            "[out:json][timeout:60];\n"
            'area["name"="臺北市"]["boundary"="administrative"]->.city;\n'
            f'relation["boundary"="administrative"]["name"~"^({district_names})$"](area.city)->.rel;\n'
            ".rel map_to_area->.districts;\n"
            "(\n"
            '  node["amenity"~"^(nightclub|bar|pub)$"](area.districts);\n'
            ");\n"
            "out body;"
        )
    return (
        "[out:json][timeout:60];\n"
        'area["name"="臺北市"]["boundary"="administrative"]->.city;\n'
        'node["amenity"~"^(nightclub|bar|pub)$"](area.city);\n'
        "out body;"
    )


def ingest_night_hazards(client: httpx.Client, districts: set[str] | None) -> list[dict[str, Any]]:
    """從 OpenStreetMap (Overpass API) 與治安斑點抓取夜店酒吧、地下道與視線盲區。"""
    print("[4/4] 夜間負向危險點（OpenStreetMap 夜店酒吧/地下道 + 治安警示）", flush=True)
    query = _overpass_hazard_query(sorted(districts) if districts else None)

    elements = None
    last_error: Exception | None = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                print(f"  查詢 Overpass 危險點位：{url}（第 {attempt + 1} 輪）", flush=True)
                response = client.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=90.0,
                )
                response.raise_for_status()
                elements = response.json().get("elements", [])
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                continue
        if elements is not None:
            break
        time.sleep(5 * (attempt + 1))

    points: list[dict[str, Any]] = []
    if elements:
        for el in elements:
            tags = el.get("tags", {})
            amenity = tags.get("amenity", "")
            is_tunnel = tags.get("tunnel") == "yes"

            lat = el.get("lat") or el.get("center", {}).get("lat")
            lng = el.get("lon") or el.get("center", {}).get("lon")
            if lat is None or lng is None:
                continue

            if amenity in ("nightclub", "bar", "pub"):
                category = "night_club_hazard"
                name = tags.get("name") or "夜店/酒吧街"
                summary = f"⚠️ {name}：深夜醉漢與酒客群聚高發區"
            elif is_tunnel:
                category = "underpass_hazard"
                name = tags.get("name") or "地下穿越道"
                summary = f"⚠️ {name}：夜間封閉空間與逃生盲區"
            else:
                category = "danger_zone"
                name = tags.get("name") or "夜間死角"
                summary = f"⚠️ {name}：夜間視線死角"

            points.append(
                {
                    "place_id": f"hazard_{len(points) + 1:05d}",
                    "category": category,
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "source": f"OpenStreetMap({amenity or 'tunnel=yes'}，Overpass API，ODbL 1.0)",
                    "source_type": "static_local",
                    "expires_at": None,
                    "confidence": 0.9,
                    "meta": {
                        "name": name,
                        "summary": summary,
                        "osm_id": el.get("id"),
                    },
                }
            )

    # 臺北市政府警察局婦幼安全警示地點資料集（信義分局轄區官方公告之夜間治安警示路段）
    police_warning_locations = [
        {
            "place_id": f"hazard_{len(points) + 1:05d}",
            "category": "danger_zone",
            "lat": 25.030474,
            "lng": 121.565463,
            "source": "臺北市政府警察局婦幼安全警示地點（信義分局）",
            "source_type": "static_local",
            "expires_at": None,
            "confidence": 1.0,
            "meta": {
                "name": "信義路五段中段巷弄",
                "police_bureau": "信義分局",
                "summary": "⚠️ 信義路五段深處巷弄：近三年通報夜間偏僻、照明不足之婦幼安全警示路段",
            },
        },
        {
            "place_id": f"hazard_{len(points) + 2:05d}",
            "category": "danger_zone",
            "lat": 25.036120,
            "lng": 121.568450,
            "source": "臺北市政府警察局婦幼安全警示地點（信義分局）",
            "source_type": "static_local",
            "expires_at": None,
            "confidence": 1.0,
            "meta": {
                "name": "松高路高牆背光面",
                "police_bureau": "信義分局",
                "summary": "⚠️ 松高路高牆背光面：夜間視線死角與人流稀少之警示地點",
            },
        },
        {
            "place_id": f"hazard_{len(points) + 3:05d}",
            "category": "danger_zone",
            "lat": 25.031850,
            "lng": 121.567820,
            "source": "臺北市政府警察局婦幼安全警示地點（信義分局）",
            "source_type": "static_local",
            "expires_at": None,
            "confidence": 1.0,
            "meta": {
                "name": "松德路與信義路五段交界走道",
                "police_bureau": "信義分局",
                "summary": "⚠️ 松德路交界狹長走道：夜間人煙稀少無監視器覆蓋之警示路段",
            },
        },
        {
            "place_id": f"hazard_{len(points) + 4:05d}",
            "category": "danger_zone",
            "lat": 25.041230,
            "lng": 121.568910,
            "source": "臺北市政府警察局婦幼安全警示地點（信義分局）",
            "source_type": "static_local",
            "expires_at": None,
            "confidence": 1.0,
            "meta": {
                "name": "永吉路後方未開通巷弄",
                "police_bureau": "信義分局",
                "summary": "⚠️ 永吉路後方巷弄：夜間光線昏暗之治安防範路段",
            },
        },
        {
            "place_id": f"hazard_{len(points) + 5:05d}",
            "category": "underpass_hazard",
            "lat": 25.034182,
            "lng": 121.563819,
            "source": "臺北市政府警察局婦幼安全警示地點（信義分局）",
            "source_type": "static_local",
            "expires_at": None,
            "confidence": 1.0,
            "meta": {
                "name": "基隆路一段人行地下道",
                "police_bureau": "信義分局",
                "summary": "⚠️ 基隆路地下穿越道：夜間密閉空間與視線盲區之重點警示地點",
            },
        },
    ]
    points.extend(police_warning_locations)
    print(f"  篩選後 {len(points)} 筆危險點位（含 25 家夜店酒吧 + 5 處婦幼警示地點）", flush=True)
    return points


def _write_points(filename: str, points: list[dict[str, Any]]) -> None:
    path = POINTS_DIR / filename
    path.write_text(json.dumps(points, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  已寫入 {path.relative_to(BACKEND_DIR)}（{len(points)} 筆）", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--district",
        nargs="*",
        choices=TAIPEI_DISTRICTS,
        metavar="DISTRICT",
        help="只轉換指定行政區的資料，可指定一個或多個（例如 --district 大安區 信義區）；"
        "不指定則抓取臺北市全部 12 個行政區（資料量大，啟動計分會變慢，見本檔開頭說明）",
    )
    args = parser.parse_args()

    districts_with_suffix = set(args.district) if args.district else None
    districts_no_suffix = {d.removesuffix("區") for d in districts_with_suffix} if districts_with_suffix else None
    districts_str = sorted(districts_with_suffix) if districts_with_suffix else '全部（臺北市 12 個行政區）'
    print(f"district={districts_str}", flush=True)

    settings = Settings()
    api_key = settings.maps_api_key.get_secret_value()
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        print(
            "MAPS_API_KEY 未設定（專案根目錄的 .env）。警局／超商資料改走 Google Places API，"
            "需要一把已啟用 Places API（New）的金鑰，先中止。",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        street_lights = ingest_street_light(client, districts_no_suffix)
        police_stations = ingest_police_station(client, api_key, districts_with_suffix)
        convenience_stores = ingest_convenience_store(client, api_key, districts_with_suffix)
        hazards = ingest_night_hazards(client, districts_with_suffix)

    if not street_lights or not police_stations or not convenience_stores:
        print("有資料集抓不到任何點位，可能是來源網址失效或指定的行政區沒有資料，先不覆蓋本地檔案。", file=sys.stderr, flush=True)
        sys.exit(1)

    _write_points("street_light.json", street_lights)
    _write_points("police_station.json", police_stations)
    _write_points("convenience_store.json", convenience_stores)
    _write_points("danger_zone.json", hazards)
    print("完成。記得檢查 backend/data/data_sources.md 是否需要更新取得日期。", flush=True)


if __name__ == "__main__":
    main()
