"""地址轉座標（AGENTS.md §9.1）。

改用 Gemini + Google Search Grounding 取代 Nominatim：讓 Gemini 用即時搜尋
結果理解地點的語意（連鎖店分館、口語地標、模糊地址），比純字串比對式的
Geocoding API 更能處理歧義。

「挑最近的候選」維持 deterministic：Gemini 只負責找出語意上可能相符的候選
座標清單，實際離 bias 最近的一筆由這裡用 haversine 公式計算決定，不讓 LLM
自己做數值判斷（呼應 AGENTS.md 原則 2：安全數值一律 deterministic 計算的
精神，這裡把同樣的原則套用到座標選擇上）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Protocol

from google.genai import types

from app.engine.geo import haversine_m
from interfaces import LatLng

_MAX_CANDIDATES = 8
_TIMEOUT_MS = 8_000

# 粗略台灣（含離島）經緯度範圍，用來擋掉明顯搜尋錯國家的幻覺結果；
# 精確的覆蓋範圍檢查仍由路徑引擎的 nearest_node 負責（§4.7）。
_TW_LAT_RANGE = (21.5, 26.5)
_TW_LNG_RANGE = (118.0, 123.0)

_PROMPT_TEMPLATE = """請用 Google 搜尋確認下面這個地點在台灣的實際座標，並列出所有語意上可能符合的候選地點（例如同名連鎖店的不同分店、容易混淆的相似地名）。

地點描述：{place_description}
{bias_hint}
規則：
- 只回傳台灣境內的地點，忽略國外同名地點。
- 每個候選附上簡短名稱與 WGS84 經緯度（小數點後至少 5 位）。
- 完全找不到符合的地點時回傳空陣列 []。
- 只能輸出一個 JSON 陣列，不要有任何其他文字、說明或 markdown 標記，格式如下：
[{{"name": "地點名稱", "lat": 25.04776, "lng": 121.51701}}]
"""


class GenAIClient(Protocol):
    aio: Any


class GeminiGroundedGeocoder:
    """用 Gemini 的 Google Search Grounding 把地點文字轉成座標。"""

    def __init__(self, client: GenAIClient, model: str) -> None:
        self._client = client
        self._model = model
        self._config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.0,
            http_options=types.HttpOptions(timeout=_TIMEOUT_MS),
        )

    async def geocode(
        self,
        place_description: str,
        bias: Optional[LatLng] = None,
    ) -> Optional[LatLng]:
        """查無結果或請求失敗一律回傳 None，交由呼叫方轉成 GEOCODING_FAILED（§6.5）。"""
        prompt = _PROMPT_TEMPLATE.format(
            place_description=place_description,
            bias_hint=self._bias_hint(bias),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                config=self._config,
            )
        except Exception:
            return None

        candidates = self._parse_candidates(response.text or "")
        if not candidates:
            return None
        if bias is None:
            return candidates[0]
        return min(candidates, key=lambda c: haversine_m(bias, c))

    @staticmethod
    def _bias_hint(bias: Optional[LatLng]) -> str:
        if bias is None:
            return ""
        return (
            f"使用者目前大約在緯度 {bias.lat:.5f}、經度 {bias.lng:.5f} 附近，"
            "僅供理解「哪一間分店／哪個地點」時參考語意，不代表答案只能在附近。\n"
        )

    @classmethod
    def _parse_candidates(cls, text: str) -> list[LatLng]:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match is None:
            return []
        try:
            raw = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(raw, list):
            return []

        candidates: list[LatLng] = []
        for item in raw[:_MAX_CANDIDATES]:
            candidate = cls._to_latlng(item)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _to_latlng(item: Any) -> Optional[LatLng]:
        if not isinstance(item, dict):
            return None
        try:
            lat = float(item["lat"])
            lng = float(item["lng"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (_TW_LAT_RANGE[0] <= lat <= _TW_LAT_RANGE[1]):
            return None
        if not (_TW_LNG_RANGE[0] <= lng <= _TW_LNG_RANGE[1]):
            return None
        return LatLng(lat=lat, lng=lng)
