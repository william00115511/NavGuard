"""Google encoded polyline algorithm 的解碼（純函式，無外部依賴）。

Routes API 的 computeRoutes 預設用這個編碼格式回傳 route.polyline.encodedPolyline
（跟舊版 Directions API 的 overview_polyline.points 是同一種格式，precision 5）。
規格：https://developers.google.com/maps/documentation/utilities/polylinealgorithm
"""

from interfaces import LatLng

_PRECISION = 1e5


def decode_polyline(encoded: str) -> list[LatLng]:
    """把 encoded polyline 字串解成一串 LatLng，順序即路徑行進方向。"""
    points: list[LatLng] = []
    index = 0
    length = len(encoded)
    lat = 0
    lng = 0

    while index < length:
        delta_lat, index = _decode_signed_value(encoded, index, length)
        lat += delta_lat
        delta_lng, index = _decode_signed_value(encoded, index, length)
        lng += delta_lng
        points.append(LatLng(lat=lat / _PRECISION, lng=lng / _PRECISION))

    return points


def _decode_signed_value(encoded: str, index: int, length: int) -> tuple[int, int]:
    """解一個 varint（一次 lat 或一次 lng 的差值），回傳（數值, 讀完後的 index）
    ——encoded polyline 每個值長度不固定，呼叫端要接著從回傳的 index 讀下一個值。"""
    result = 0
    shift = 0
    while True:
        if index >= length:
            raise ValueError("encoded polyline 字串在讀到完整數值前就結束了")
        b = ord(encoded[index]) - 63
        index += 1
        result |= (b & 0x1F) << shift
        shift += 5
        if b < 0x20:
            break
    value = ~(result >> 1) if (result & 1) else (result >> 1)
    return value, index


def encode_polyline(points: list[LatLng]) -> str:
    """解碼的反向操作，只給測試用來做 round-trip 驗證。"""
    encoded_chars: list[str] = []
    prev_lat = 0
    prev_lng = 0
    for point in points:
        lat = round(point.lat * _PRECISION)
        lng = round(point.lng * _PRECISION)
        encoded_chars.append(_encode_signed_value(lat - prev_lat))
        encoded_chars.append(_encode_signed_value(lng - prev_lng))
        prev_lat, prev_lng = lat, lng
    return "".join(encoded_chars)


def _encode_signed_value(value: int) -> str:
    shifted = ~(value << 1) if value < 0 else (value << 1)
    chars: list[str] = []
    while shifted >= 0x20:
        chars.append(chr((0x20 | (shifted & 0x1F)) + 63))
        shifted >>= 5
    chars.append(chr(shifted + 63))
    return "".join(chars)
