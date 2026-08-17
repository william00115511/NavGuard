import asyncio
from typing import Any

from app.geocoding.google_places_geocoder import GooglePlacesGeocoder
from interfaces import LatLng


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, data: Any) -> None:
        self._data = data

    def json(self) -> Any:
        return self._data


class FakeHTTPClient:
    def __init__(self, data: Any = None, exc: Exception | None = None) -> None:
        self._data = data
        self._exc = exc
        self.calls: list[dict] = []

    async def post(self, url: str, *, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self._exc is not None:
            raise self._exc
        return FakeResponse(self._data)


def places(*locations: tuple[float, float]) -> dict:
    return {"places": [{"location": {"latitude": lat, "longitude": lng}} for lat, lng in locations]}


def test_geocode_picks_nearest_candidate_to_bias() -> None:
    client = FakeHTTPClient(places((25.04000, 121.51000), (25.20000, 121.70000)))
    geocoder = GooglePlacesGeocoder("key", client=client)
    bias = LatLng(lat=25.04100, lng=121.51100)

    result = run(geocoder.geocode("新光三越", bias=bias))

    assert result == LatLng(lat=25.04, lng=121.51)


def test_geocode_without_bias_returns_first_candidate() -> None:
    client = FakeHTTPClient(places((25.0478, 121.5319)))
    geocoder = GooglePlacesGeocoder("key", client=client)

    result = run(geocoder.geocode("台北車站"))

    assert result == LatLng(lat=25.0478, lng=121.5319)


def test_geocode_returns_none_for_empty_candidates() -> None:
    client = FakeHTTPClient({"places": []})
    geocoder = GooglePlacesGeocoder("key", client=client)

    assert run(geocoder.geocode("查無此地")) is None


def test_geocode_returns_none_when_request_fails() -> None:
    client = FakeHTTPClient(exc=RuntimeError("boom"))
    geocoder = GooglePlacesGeocoder("key", client=client)

    assert run(geocoder.geocode("任何地點")) is None


def test_geocode_filters_out_of_taiwan_candidates() -> None:
    client = FakeHTTPClient(places((35.6812, 139.7671), (25.0330, 121.5654)))
    geocoder = GooglePlacesGeocoder("key", client=client)

    result = run(geocoder.geocode("同名店"))

    assert result == LatLng(lat=25.0330, lng=121.5654)


def test_geocode_returns_none_for_malformed_response() -> None:
    client = FakeHTTPClient({"error": "REQUEST_DENIED"})
    geocoder = GooglePlacesGeocoder("key", client=client)

    assert run(geocoder.geocode("不存在的地方")) is None


def test_geocode_sends_location_bias_when_bias_given() -> None:
    client = FakeHTTPClient(places((25.0, 121.5)))
    geocoder = GooglePlacesGeocoder("key", client=client)
    bias = LatLng(lat=25.04100, lng=121.51100)

    run(geocoder.geocode("新光三越", bias=bias))

    sent_body = client.calls[0]["json"]
    assert sent_body["locationBias"]["circle"]["center"] == {
        "latitude": bias.lat,
        "longitude": bias.lng,
    }
