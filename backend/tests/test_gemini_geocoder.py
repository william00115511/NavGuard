import asyncio
from types import SimpleNamespace

from app.geocoding.gemini_geocoder import GeminiGroundedGeocoder
from interfaces import LatLng


def run(coro):
    return asyncio.run(coro)


class FakeModels:
    def __init__(self, response=None, exc=None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.response


class FakeClient:
    def __init__(self, response=None, exc=None) -> None:
        self.models = FakeModels(response, exc)
        self.aio = SimpleNamespace(models=self.models)


def make_response(text: str):
    return SimpleNamespace(text=text)


def test_geocode_picks_nearest_candidate_to_bias() -> None:
    text = (
        "這是說明文字，不是 JSON。\n"
        '[{"name": "分店A", "lat": 25.04000, "lng": 121.51000}, '
        '{"name": "分店B", "lat": 25.20000, "lng": 121.70000}]'
    )
    client = FakeClient(make_response(text))
    geocoder = GeminiGroundedGeocoder(client, "gemini-2.5-flash")
    bias = LatLng(lat=25.04100, lng=121.51100)

    result = run(geocoder.geocode("連鎖店", bias=bias))

    assert result == LatLng(lat=25.04, lng=121.51)


def test_geocode_without_bias_returns_first_candidate() -> None:
    text = '[{"name": "地點", "lat": 25.0478, "lng": 121.5319}]'
    client = FakeClient(make_response(text))
    geocoder = GeminiGroundedGeocoder(client, "gemini-2.5-flash")

    result = run(geocoder.geocode("台北車站"))

    assert result == LatLng(lat=25.0478, lng=121.5319)


def test_geocode_returns_none_for_empty_candidates() -> None:
    client = FakeClient(make_response("[]"))
    geocoder = GeminiGroundedGeocoder(client, "gemini-2.5-flash")

    assert run(geocoder.geocode("查無此地")) is None


def test_geocode_returns_none_when_model_request_fails() -> None:
    client = FakeClient(exc=RuntimeError("boom"))
    geocoder = GeminiGroundedGeocoder(client, "gemini-2.5-flash")

    assert run(geocoder.geocode("任何地點")) is None


def test_geocode_filters_out_of_taiwan_candidates() -> None:
    text = (
        '[{"name": "國外同名店", "lat": 35.6812, "lng": 139.7671}, '
        '{"name": "台灣分店", "lat": 25.0330, "lng": 121.5654}]'
    )
    client = FakeClient(make_response(text))
    geocoder = GeminiGroundedGeocoder(client, "gemini-2.5-flash")

    result = run(geocoder.geocode("同名店"))

    assert result == LatLng(lat=25.0330, lng=121.5654)


def test_geocode_ignores_malformed_json() -> None:
    client = FakeClient(make_response("抱歉，我找不到這個地點。"))
    geocoder = GeminiGroundedGeocoder(client, "gemini-2.5-flash")

    assert run(geocoder.geocode("不存在的地方")) is None
