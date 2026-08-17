"""一次性／可重跑的開放資料轉換腳本（AGENTS.md §3.1）。

從政府開放資料 API 直接下載原始資料，轉成 §3.2 統一點位格式，覆蓋
`backend/data/points/` 下對應的本地檔案。執行期系統不會呼叫這支腳本，
資料要更新時由人手動重跑即可。

涵蓋範圍（--district）：
預設（不加 --district）會抓臺北市全部 12 個行政區的資料。這不只是為了
路燈那 14 萬筆資料的下載/解析效能，更重要的是 `EdgeSafetyIndex` 在
啟動時對每條 edge 的每個取樣點都會掃過全部靜態點位、沒有空間索引
（app/engine/safety.py 的 raw_edge_score），全臺北市三類點位一次全塞
下去（14萬 + 3000 + 105）實測會把啟動時間拖到 8 秒以上；只轉換展示用
到的行政區（例如 `--district 中正區 大安區`）可以把三類合計壓到幾千筆，
啟動幾乎感覺不到。

用法：
    cd backend
    .venv/Scripts/python.exe scripts/ingest_open_data.py
    .venv/Scripts/python.exe scripts/ingest_open_data.py --district 大安區 信義區
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import truststore
from pyproj import Transformer

# tgos.tw（警政署資料下載點）的憑證缺 Subject Key Identifier 擴充欄位，
# Python 內建的 certifi 信任串會擋下來，但作業系統的信任庫可以接受
# （curl／瀏覽器都能正常連線）。改用 OS 信任庫做驗證，而不是關掉驗證。
truststore.inject_into_ssl()

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
POINTS_DIR = DATA_DIR / "points"

_TWD97_TO_WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)

_HTTP_TIMEOUT = 60.0
_USER_AGENT = "Safeway-NightWalkSafety-DataIngest/0.1 (offline conversion script)"

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

# 警政署地址欄固定以「臺北市／台北市」+ 行政區名稱開頭，例如「臺北市大安區仁愛路3段2號」。
_DISTRICT_ADDR_RE = re.compile(r"(?:臺北市|台北市)([一-鿿]{2,3}區)")

STREETLIGHT_URL = "https://tppkl.blob.core.windows.net/blobfs/TaipeiLight.csv"
POLICE_ZIP_URL = "https://www.tgos.tw/tgos/VirtualDir/Product/9927eb8a-efed-40c0-8bc4-83121ad6834a/1150729.zip"
# 公用 Overpass 主機常常忙碌，準備幾個鏡像站輪流試。
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]


def _twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lng, lat = _TWD97_TO_WGS84.transform(x, y)
    return lat, lng


def _get(client: httpx.Client, url: str) -> httpx.Response:
    print(f"  下載中：{url}")
    response = client.get(url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True)
    response.raise_for_status()
    return response


# ---------- 路燈 ----------


def ingest_street_light(client: httpx.Client, districts: set[str] | None) -> list[dict[str, Any]]:
    """districts 為不含「區」字的行政區名稱集合（對應 CSV 的 Dist 欄位），None 表示不篩選。"""
    print("[1/3] 路燈資料（臺北市工務局公園路燈工程管理處）")
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
                "id": f"streetlight_{len(points) + 1:05d}",
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


# ---------- 警局 ----------


def ingest_police_station(client: httpx.Client, districts: set[str] | None) -> list[dict[str, Any]]:
    """districts 為含「區」字的行政區名稱集合，None 表示不篩選（仍只留臺北市的資料）。"""
    print("[2/3] 警察機關資料（內政部警政署）")
    response = _get(client, POLICE_ZIP_URL)

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        # 壓縮檔裡還有一份 manifest.csv（純中繼資料，非點位），要排除掉。
        csv_name = next(
            name for name in zf.namelist() if name.lower().endswith(".csv") and "manifest" not in name.lower()
        )
        raw_bytes = zf.read(csv_name)

    reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-8-sig")))
    points: list[dict[str, Any]] = []
    total = 0
    for row in reader:
        total += 1
        address = row.get("地址", "")
        match = _DISTRICT_ADDR_RE.match(address)
        if match is None:
            continue  # 非臺北市地址
        district = match.group(1)
        if districts is not None and district not in districts:
            continue
        try:
            x, y = float(row["POINT_X"]), float(row["POINT_Y"])
        except (KeyError, ValueError, TypeError):
            continue
        lat, lng = _twd97_to_wgs84(x, y)
        points.append(
            {
                "id": f"police_{len(points) + 1:05d}",
                "category": "police_station",
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "source": "各縣(市)警察(分)局暨所屬分駐(派出)所地址資料（內政部警政署，data.gov.tw）",
                "source_type": "static_local",
                "expires_at": None,
                "confidence": 1.0,
                "meta": {
                    "name": row.get("中文單位名稱", "").strip(),
                    "name_en": row.get("英文單位名稱", "").strip(),
                    "address": address.strip(),
                    "phone": row.get("電話", "").strip(),
                },
            }
        )
    print(f"  全國 {total} 筆 -> 篩選後 {len(points)} 筆")
    return points


# ---------- 便利商店（help_point）----------


def _overpass_query(districts: list[str] | None) -> str:
    if districts:
        district_areas = "".join(
            f'  area(area.city)["boundary"="administrative"]["name"="{d}"];\n' for d in districts
        )
        return (
            "[out:json][timeout:60];\n"
            'area["name"="臺北市"]["boundary"="administrative"]->.city;\n'
            f"(\n{district_areas})->.districts;\n"
            'node["shop"="convenience"](area.districts);\n'
            "out body;"
        )
    return (
        "[out:json][timeout:60];\n"
        'area["name"="臺北市"]["boundary"="administrative"]->.city;\n'
        'node["shop"="convenience"](area.city);\n'
        "out body;"
    )


def ingest_convenience_store(client: httpx.Client, districts: set[str] | None) -> list[dict[str, Any]]:
    """districts 為含「區」字的行政區名稱集合，None 表示不篩選（全臺北市）。"""
    print("[3/3] 便利商店資料（OpenStreetMap，shop=convenience）")
    query = _overpass_query(sorted(districts) if districts else None)

    elements = None
    last_error: Exception | None = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                print(f"  查詢 Overpass：{url}（第 {attempt + 1} 輪）")
                response = client.post(
                    url,
                    data={"data": query},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=90.0,
                )
                response.raise_for_status()
                elements = response.json()["elements"]
                break
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                continue
        if elements is not None:
            break
        time.sleep(5 * (attempt + 1))

    if elements is None:
        raise RuntimeError(f"所有 Overpass 端點都失敗了，最後一次錯誤：{last_error}")

    points: list[dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags", {})
        points.append(
            {
                "id": f"helppoint_{len(points) + 1:05d}",
                "category": "help_point",
                "lat": round(el["lat"], 6),
                "lng": round(el["lon"], 6),
                "source": "OpenStreetMap contributors（shop=convenience，Overpass API，ODbL 1.0）",
                "source_type": "static_local",
                "expires_at": None,
                "confidence": 0.9,  # OSM 為志願者維護資料，非官方普查，信心值略低於政府資料
                "meta": {
                    "name": tags.get("name"),
                    "brand": tags.get("brand"),
                    "osm_id": el.get("id"),
                },
            }
        )
    print(f"  篩選後 {len(points)} 筆")
    return points


def _write_points(filename: str, points: list[dict[str, Any]]) -> None:
    path = POINTS_DIR / filename
    path.write_text(json.dumps(points, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  已寫入 {path.relative_to(BACKEND_DIR)}（{len(points)} 筆）")


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
    print(f"district={sorted(districts_with_suffix) if districts_with_suffix else '全部（臺北市 12 個行政區）'}")

    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        street_lights = ingest_street_light(client, districts_no_suffix)
        police_stations = ingest_police_station(client, districts_with_suffix)
        convenience_stores = ingest_convenience_store(client, districts_with_suffix)

    if not street_lights or not police_stations or not convenience_stores:
        print("有資料集抓不到任何點位，可能是來源網址失效或指定的行政區沒有資料，先不覆蓋本地檔案。", file=sys.stderr)
        sys.exit(1)

    _write_points("street_light.json", street_lights)
    _write_points("police_station.json", police_stations)
    _write_points("help_point.json", convenience_stores)
    print("完成。記得檢查 backend/data/data_sources.md 是否需要更新取得日期。")


if __name__ == "__main__":
    main()
