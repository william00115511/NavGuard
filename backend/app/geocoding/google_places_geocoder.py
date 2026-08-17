"""地址轉座標（AGENTS.md §9.1）。

改用 Google Places API（New）的 Text Search 取代 Gemini + Google Search
Grounding：Gemini 在上游（gemini_chat_service.py 的 function calling）已經把
使用者的自然語言輸入解析成明確的地點描述文字（place_description），這裡只需要
把文字丟給專門的地點搜尋 API，交由它做模糊比對（連鎖店分館、口語地標），
不必再依賴 LLM 自己生成座標數值。Text Search 而非 Geocoding API：後者偏地址
級的精確比對，遇到「新光三越」這類需要列出多間分店的模糊查詢效果較差。

「挑最近的候選」維持 deterministic：Places API 回傳的候選地點清單中，實際離
bias（使用者目前位置）最近的一筆由這裡用 haversine 公式計算決定，呼應
AGENTS.md 原則 2（安全數值一律 deterministic 計算，不讓 API 排序替我們做決定）。
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

import httpx

from app.engine.geo import haversine_m
from interfaces import LatLng

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_TIMEOUT_S = 8.0
_PAGE_SIZE = 20
# locationBias 只是讓搜尋結果排序偏向 bias 附近，不是硬性篩選（超商、超市等
# 分店數遠超過 pageSize 上限的連鎖店，沒有這層偏向可能連最近的分店都擠不進
# 回傳清單），語意上對應舊版 Gemini prompt 裡「僅供理解語意，不代表答案只能
# 在附近」的 bias_hint。
_LOCATION_BIAS_RADIUS_M = 50_000.0

# 粗略台灣（含離島）經緯度範圍，擋掉明顯搜尋錯國家的同名地點。
_TW_LAT_RANGE = (21.5, 26.5)
_TW_LNG_RANGE = (118.0, 123.0)


class HTTPClient(Protocol):
    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> Any: ...


class GooglePlacesGeocoder:
    """用 Google Places API 的 Text Search 把地點文字轉成座標。"""

    def __init__(self, api_key: str, client: Optional[HTTPClient] = None) -> None:
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT_S)

    async def geocode(
        self,
        place_description: str,
        bias: Optional[LatLng] = None,
    ) -> Optional[LatLng]:
        """查無結果或請求失敗一律回傳 None，交由呼叫方轉成 GEOCODING_FAILED（§6.5）。"""
        body: dict[str, Any] = {
            "textQuery": place_description,
            "languageCode": "zh-TW",
            "regionCode": "TW",
            "pageSize": _PAGE_SIZE,
        }
        if bias is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": bias.lat, "longitude": bias.lng},
                    "radius": _LOCATION_BIAS_RADIUS_M,
                }
            }

        try:
            response = await self._client.post(
                _SEARCH_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": self._api_key,
                    "X-Goog-FieldMask": "places.location",
                },
                json=body,
            )
            data = response.json()
        except Exception:
            return None

        candidates = self._parse_candidates(data)
        if not candidates:
            return None
        if bias is None:
            return candidates[0]
        return min(candidates, key=lambda c: haversine_m(bias, c))

    @classmethod
    def _parse_candidates(cls, data: Any) -> list[LatLng]:
        if not isinstance(data, dict):
            return []
        places = data.get("places")
        if not isinstance(places, list):
            return []

        candidates: list[LatLng] = []
        for item in places:
            candidate = cls._to_latlng(item)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _to_latlng(item: Any) -> Optional[LatLng]:
        if not isinstance(item, dict):
            return None
        try:
            location = item["location"]
            lat = float(location["latitude"])
            lng = float(location["longitude"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (_TW_LAT_RANGE[0] <= lat <= _TW_LAT_RANGE[1]):
            return None
        if not (_TW_LNG_RANGE[0] <= lng <= _TW_LNG_RANGE[1]):
            return None
        return LatLng(lat=lat, lng=lng)
