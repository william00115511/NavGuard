"""座標相關的純數學工具，不依賴任何資料層或框架。"""

import math

from inner_interface import LatLng

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: LatLng, b: LatLng) -> float:
    """兩經緯度座標間的地表距離（公尺）。"""
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def midpoint(a: LatLng, b: LatLng) -> LatLng:
    """簡化中點（edge 通常很短，直接平均即可，不需球面中點公式）。"""
    return LatLng(lat=(a.lat + b.lat) / 2, lng=(a.lng + b.lng) / 2)
