"""路徑簡化（AGENTS.md §7.3）。

引擎算出的 path_coordinates 可能有數十甚至上百個點，不能直接全塞進
Google Maps 的 waypoints。單純的 Douglas-Peucker 會保留「形狀轉折」，
但 §7.3 要求的更多一步：Google Maps 會用它自己的演算法連接相鄰兩個中繼點，
所以必須**優先保留安全路線明顯偏離最快路線的那幾個轉折點**，否則簡化後
使用者在 Google Maps 上又會被導回原本的最短路線。

作法：先算出每個點相對於 α=0 最快路線的偏離距離，把偏離最大的幾個點設為
強制保留的錨點，再用 Douglas-Peucker 簡化錨點之間的區段。
"""

from typing import Sequence

from interfaces import LatLng


def _perpendicular_distance(point: LatLng, start: LatLng, end: LatLng) -> float:
    """點到線段所在直線的垂直距離。範圍是步行等級的小區域，用平面近似即可。"""
    if start.lat == end.lat and start.lng == end.lng:
        return ((point.lat - start.lat) ** 2 + (point.lng - start.lng) ** 2) ** 0.5

    numerator = abs(
        (end.lat - start.lat) * (start.lng - point.lng)
        - (start.lat - point.lat) * (end.lng - start.lng)
    )
    denominator = ((end.lat - start.lat) ** 2 + (end.lng - start.lng) ** 2) ** 0.5
    return numerator / denominator


def _distance_to_segment(point: LatLng, start: LatLng, end: LatLng) -> float:
    """點到**線段**（有端點，非無限延伸）的最短距離。"""
    dlat, dlng = end.lat - start.lat, end.lng - start.lng
    length_sq = dlat * dlat + dlng * dlng
    if length_sq == 0:
        return ((point.lat - start.lat) ** 2 + (point.lng - start.lng) ** 2) ** 0.5

    t = ((point.lat - start.lat) * dlat + (point.lng - start.lng) * dlng) / length_sq
    t = max(0.0, min(1.0, t))
    nearest_lat = start.lat + t * dlat
    nearest_lng = start.lng + t * dlng
    return ((point.lat - nearest_lat) ** 2 + (point.lng - nearest_lng) ** 2) ** 0.5


def distance_to_polyline(point: LatLng, polyline: Sequence[LatLng]) -> float:
    """點到整條折線的最短距離；折線少於兩點時退化成點對點距離。"""
    if not polyline:
        return 0.0
    if len(polyline) == 1:
        return ((point.lat - polyline[0].lat) ** 2 + (point.lng - polyline[0].lng) ** 2) ** 0.5
    return min(
        _distance_to_segment(point, a, b) for a, b in zip(polyline, polyline[1:])
    )


def douglas_peucker(points: list[LatLng], epsilon: float) -> list[LatLng]:
    if len(points) < 3:
        return list(points)

    start, end = points[0], points[-1]
    max_dist = -1.0
    max_index = 0
    for i in range(1, len(points) - 1):
        dist = _perpendicular_distance(points[i], start, end)
        if dist > max_dist:
            max_dist = dist
            max_index = i

    if max_dist > epsilon:
        left = douglas_peucker(points[: max_index + 1], epsilon)
        right = douglas_peucker(points[max_index:], epsilon)
        return left[:-1] + right
    return [start, end]


def simplify_to_max_points(points: list[LatLng], max_points: int) -> list[LatLng]:
    """二分搜尋 epsilon，直到簡化後的點數（含頭尾）<= max_points。"""
    if len(points) <= max_points:
        return list(points)

    lo, hi = 0.0, 1.0  # 經緯度尺度下 1.0 度遠超本地展示範圍，保證收斂到 <= max_points
    for _ in range(40):
        mid = (lo + hi) / 2
        if len(douglas_peucker(points, mid)) > max_points:
            lo = mid
        else:
            hi = mid
    return douglas_peucker(points, hi)


def simplify_preserving_divergence(
    points: list[LatLng],
    reference: Sequence[LatLng],
    max_points: int,
) -> list[LatLng]:
    """簡化 points，但強制保留它偏離 reference（最快路線）最遠的那幾個點。"""
    if len(points) <= max_points or max_points < 2:
        return simplify_to_max_points(points, max_points)
    if len(reference) < 2:
        return simplify_to_max_points(points, max_points)

    divergence = sorted(
        ((distance_to_polyline(p, reference), i) for i, p in enumerate(points[1:-1], start=1)),
        reverse=True,
    )
    # 一半的預算留給分歧錨點，另一半交給 Douglas-Peucker 保留整體形狀。
    anchor_budget = (max_points - 2) // 2
    anchors = sorted({0, len(points) - 1} | {i for dist, i in divergence[:anchor_budget] if dist > 0})

    interior_counts = [b - a - 1 for a, b in zip(anchors, anchors[1:])]
    total_interior = sum(interior_counts) or 1
    spare = max_points - len(anchors)

    result = [points[anchors[0]]]
    for (a, b), interior in zip(zip(anchors, anchors[1:]), interior_counts):
        extra = spare * interior // total_interior
        segment = simplify_to_max_points(points[a : b + 1], 2 + extra)
        result.extend(segment[1:])
    return result
