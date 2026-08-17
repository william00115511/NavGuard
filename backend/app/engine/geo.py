"""座標相關的純數學工具，不依賴任何資料層或框架。"""

import math

from interfaces import LatLng

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: LatLng, b: LatLng) -> float:
    """兩經緯度座標間的地表距離（公尺）。"""
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def interpolate(a: LatLng, b: LatLng, t: float) -> LatLng:
    """a 到 b 之間比例 t 處的座標。展示範圍是步行等級的小區域，線性內插即可。"""
    return LatLng(lat=a.lat + (b.lat - a.lat) * t, lng=a.lng + (b.lng - a.lng) * t)


def midpoint(a: LatLng, b: LatLng) -> LatLng:
    return interpolate(a, b, 0.5)


def sample_along(a: LatLng, b: LatLng, interval_m: float) -> tuple[LatLng, ...]:
    """沿 a→b 每隔 interval_m 取一個樣本點（AGENTS.md §4.2）。

    取樣點放在每個等分區段的中心，而不是端點：端點是路口，會被相鄰的
    edge 共用而重複計入；區段中心才能代表「這段路」本身的環境。
    """
    length_m = haversine_m(a, b)
    count = max(1, math.ceil(length_m / interval_m)) if interval_m > 0 else 1
    return tuple(interpolate(a, b, (i + 0.5) / count) for i in range(count))
