"""地址轉座標（AGENTS.md §9.1）。

MVP 選用免費的 OpenStreetMap Nominatim；未來要換 Google Geocoding API 時，
只需要另外寫一個同樣簽章的 async 函式替換掉，不影響呼叫方
（app/engine/route_engine.py 的 RouteEngine.geocode）。

Nominatim 的使用條款限制 1 req/s，動態點位可能一次要查多筆，所以這裡用一個
process 內的 async lock 排隊，避免被擋。
"""

import asyncio
import time
from typing import Optional

import httpx

from inner_interface import LatLng

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "Safeway-NightWalkSafety/0.1 (hackathon demo)"
_MIN_INTERVAL_S = 1.0  # Nominatim Usage Policy
_BIAS_DEGREES = 0.15  # 約 15 公里，用來把搜尋結果偏向使用者附近

_throttle_lock = asyncio.Lock()
_last_request_at = 0.0


async def _throttle() -> None:
    global _last_request_at
    async with _throttle_lock:
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


async def geocode_with_nominatim(
    place_description: str,
    bias: Optional[LatLng] = None,
    timeout: float = 5.0,
) -> Optional[LatLng]:
    """查無結果或請求失敗一律回傳 None，交由呼叫方轉成 GEOCODING_FAILED（§6.5）。"""
    params: dict[str, str | int] = {"q": place_description, "format": "json", "limit": 1}
    if bias is not None:
        params["viewbox"] = (
            f"{bias.lng - _BIAS_DEGREES},{bias.lat + _BIAS_DEGREES},"
            f"{bias.lng + _BIAS_DEGREES},{bias.lat - _BIAS_DEGREES}"
        )
        # bounded=0：只是偏好這個範圍，範圍外仍可回結果，覆蓋範圍檢查由引擎做。
        params["bounded"] = 0

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _NOMINATIM_URL, params=params, headers={"User-Agent": _USER_AGENT}
            )
            response.raise_for_status()
            results = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    if not results:
        return None
    first = results[0]
    return LatLng(lat=float(first["lat"]), lng=float(first["lon"]))
