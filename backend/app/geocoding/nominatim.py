"""地址轉座標（ForAI.md 4.4）。

MVP 選用免費的 OpenStreetMap Nominatim；未來要換 Google Geocoding API
時，只需要另外寫一個同樣簽章（str -> Optional[LatLng]）的函式替換掉，
不影響呼叫方（app/engine/route_engine.py 的 RouteEngine.geocode）。
"""

from typing import Optional

import httpx

from inner_interface import LatLng

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "Safeway-NightWalkSafety/0.1 (hackathon demo)"


def geocode_with_nominatim(place_description: str, timeout: float = 5.0) -> Optional[LatLng]:
    """查無結果或請求失敗一律回傳 None，交由呼叫方轉成 GEOCODING_FAILED（4.5.4節）。"""
    try:
        response = httpx.get(
            _NOMINATIM_URL,
            params={"q": place_description, "format": "json", "limit": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    results = response.json()
    if not results:
        return None

    first = results[0]
    return LatLng(lat=float(first["lat"]), lng=float(first["lon"]))
