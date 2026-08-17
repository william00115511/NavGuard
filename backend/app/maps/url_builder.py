"""Google Maps 方向連結產生（AGENTS.md §7.1 / §7.2）。

這個 URL 只是「使用者想改用 Google Maps 實際導航」時的交接管道；路線視覺化
由前端自己畫 polyline，不依賴這條連結（§7）。
"""

from typing import Sequence

from app.maps.simplify import simplify_preserving_divergence
from inner_interface import LatLng

# §7.2：中繼點在一般消費端網頁／App 實測穩定支援約 9~10 個，保守取 9。
MAX_WAYPOINTS = 9
MAX_URL_POINTS = MAX_WAYPOINTS + 2  # 起點 + 中繼點 + 終點


def _format_coord(point: LatLng) -> str:
    return f"{point.lat},{point.lng}"


def build_google_maps_url(
    path_coordinates: Sequence[LatLng],
    reference_path: Sequence[LatLng] = (),
) -> str:
    """不需 API Key 的公開路線連結，固定 travelmode=walking（夜間步行導航）。

    reference_path 是 α=0 的最快路線，用來決定哪些轉折點必須保留（§7.3）。
    """
    if not path_coordinates:
        raise ValueError("path_coordinates 不可為空")

    if len(path_coordinates) == 1:
        # 起訖點吸附到同一個節點（例如兩個地址在同一個路口）。這不是錯誤，
        # 產生一條起訖相同的連結即可，不要讓整個請求失敗。
        origin = destination = path_coordinates[0]
        waypoints: list[LatLng] = []
    else:
        simplified = simplify_preserving_divergence(
            list(path_coordinates), reference_path, MAX_URL_POINTS
        )
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
