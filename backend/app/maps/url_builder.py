"""Google Maps 方向連結產生（ForAI.md 5.1 / 5.2）。"""

from app.maps.simplify import simplify_to_max_points
from inner_interface import LatLng

# 5.2節：中繼點一般消費端實測穩定支援約 9~10 個，保守取 9。
MAX_WAYPOINTS = 9


def _format_coord(point: LatLng) -> str:
    return f"{point.lat},{point.lng}"


def build_google_maps_url(path_coordinates: list[LatLng]) -> str:
    """不需 API Key 的公開路線連結，固定 travelmode=walking（夜間步行導航）。"""
    if len(path_coordinates) < 2:
        raise ValueError("path_coordinates 至少需要兩個點（起點與終點）")

    simplified = simplify_to_max_points(path_coordinates, MAX_WAYPOINTS + 2)
    origin, *waypoints, destination = simplified

    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={_format_coord(origin)}"
        f"&destination={_format_coord(destination)}"
    )
    if waypoints:
        url += "&waypoints=" + "|".join(_format_coord(p) for p in waypoints)
    url += "&travelmode=walking"
    return url
