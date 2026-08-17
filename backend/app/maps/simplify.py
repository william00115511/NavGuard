"""Douglas-Peucker 路徑簡化（ForAI.md 5.3）。

引擎算出的 path_coordinates 可能有數十甚至上百個點，不能直接全塞進
Google Maps 的 waypoints；用 DP 保留能代表轉折形狀的關鍵點、去除幾乎
共線的中間點。DP 對直線段中點的容忍度低、對明顯轉彎的容忍度高，
剛好符合「優先保留安全路徑偏離最快路徑的轉折點」的需求。
"""

from inner_interface import LatLng


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
    simplified = points
    for _ in range(40):
        mid = (lo + hi) / 2
        simplified = douglas_peucker(points, mid)
        if len(simplified) > max_points:
            lo = mid
        else:
            hi = mid
    return douglas_peucker(points, hi)
