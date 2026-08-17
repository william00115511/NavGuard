"""呼叫 Google Routes API（computeRoutes，walking mode）取得 Google 實際會
導航的路線，解碼成座標序列。

跟 app/geocoding/google_places_geocoder.py 一樣的風格：定義一個 Protocol
給呼叫端（runner.py）依賴，真正的 HTTP 實作與測試用的 Fake 都實作同一個
介面，測試不需要真的打 API。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import httpx

from app.engine.geo import haversine_m
from evaluation.config import GOOGLE_CACHE_DIR, GOOGLE_ROUTES_TIMEOUT_S, GOOGLE_ROUTES_URL
from evaluation.polyline import decode_polyline
from interfaces import LatLng

_FIELD_MASK = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"


@dataclass(frozen=True)
class GoogleRouteResult:
    polyline: tuple[LatLng, ...]
    distance_m: float
    duration_s: float


class GoogleRoutesClient(Protocol):
    async def compute_walking_route(
        self, origin: LatLng, destination: LatLng
    ) -> Optional[GoogleRouteResult]:
        """回傳 None 代表 Google 對這組起訖點找不到路線。"""
        ...


class HttpGoogleRoutesClient:
    """真正打 Google Routes API 的實作。需要 MAPS_API_KEY（app/config.py），
    且該 key 所屬的 GCP 專案要另外啟用 Routes API（跟 Places API 是不同的
    API，需分別啟用；金鑰共用同一把）。"""

    def __init__(self, api_key: str, client: Optional[httpx.AsyncClient] = None) -> None:
        if not api_key:
            raise ValueError("HttpGoogleRoutesClient 需要 MAPS_API_KEY，目前是空字串")
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=GOOGLE_ROUTES_TIMEOUT_S)

    async def compute_walking_route(
        self, origin: LatLng, destination: LatLng
    ) -> Optional[GoogleRouteResult]:
        body = {
            "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}}},
            "destination": {
                "location": {"latLng": {"latitude": destination.lat, "longitude": destination.lng}}
            },
            "travelMode": "WALK",
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        response = await self._client.post(GOOGLE_ROUTES_URL, headers=headers, json=body)
        response.raise_for_status()
        return _parse_response(response.json())


def _parse_response(data: dict) -> Optional[GoogleRouteResult]:
    routes = data.get("routes") or []
    if not routes:
        return None
    route = routes[0]
    encoded = (route.get("polyline") or {}).get("encodedPolyline")
    if not encoded:
        return None
    polyline = decode_polyline(encoded)
    if len(polyline) < 2:
        return None
    return GoogleRouteResult(
        polyline=tuple(polyline),
        distance_m=float(route.get("distanceMeters", 0.0)),
        duration_s=_parse_duration_seconds(route.get("duration", "0s")),
    )


def _parse_duration_seconds(duration: str) -> float:
    """Routes API 的 duration 是形如 "123s" 的字串。"""
    return float(duration.rstrip("s")) if duration else 0.0


class CachingGoogleRoutesClient:
    """包住另一個 GoogleRoutesClient，把回應快取到本地檔案（依起訖座標算
    檔名），重跑報表產出不必重打 API，也讓示範現場不需要即時 key 就能重放
    既有結果（AGENTS.md 精神：可重現、可稽核）。"""

    def __init__(self, inner: GoogleRoutesClient, cache_dir: Path = GOOGLE_CACHE_DIR) -> None:
        self._inner = inner
        self._cache_dir = cache_dir

    async def compute_walking_route(
        self, origin: LatLng, destination: LatLng
    ) -> Optional[GoogleRouteResult]:
        cache_path = self._cache_path(origin, destination)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached

        result = await self._inner.compute_walking_route(origin, destination)
        self._write_cache(cache_path, result)
        return result

    def _cache_path(self, origin: LatLng, destination: LatLng) -> Path:
        key = f"{origin.lat:.6f},{origin.lng:.6f}->{destination.lat:.6f},{destination.lng:.6f}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return self._cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> Optional[GoogleRouteResult]:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw is None:
            return None
        return GoogleRouteResult(
            polyline=tuple(LatLng(lat=p["lat"], lng=p["lng"]) for p in raw["polyline"]),
            distance_m=raw["distance_m"],
            duration_s=raw["duration_s"],
        )

    def _write_cache(self, path: Path, result: Optional[GoogleRouteResult]) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            path.write_text("null", encoding="utf-8")
            return
        raw = {
            "polyline": [{"lat": p.lat, "lng": p.lng} for p in result.polyline],
            "distance_m": result.distance_m,
            "duration_s": result.duration_s,
        }
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


class FakeGoogleRoutesClient:
    """測試與 dry-run 用：不打任何網路請求，回傳呼叫端在建構時給的固定路線
    （預設是起訖點之間的直線，附帶幾個中繼點模擬真實 polyline 的密度）。"""

    def __init__(self, fixed_result: Optional[GoogleRouteResult] = None) -> None:
        self._fixed_result = fixed_result

    async def compute_walking_route(
        self, origin: LatLng, destination: LatLng
    ) -> Optional[GoogleRouteResult]:
        if self._fixed_result is not None:
            return self._fixed_result

        steps = 8
        polyline = tuple(
            LatLng(
                lat=origin.lat + (destination.lat - origin.lat) * i / steps,
                lng=origin.lng + (destination.lng - origin.lng) * i / steps,
            )
            for i in range(steps + 1)
        )
        distance_m = sum(haversine_m(a, b) for a, b in zip(polyline, polyline[1:]))
        return GoogleRouteResult(polyline=polyline, distance_m=distance_m, duration_s=distance_m / 1.3)
