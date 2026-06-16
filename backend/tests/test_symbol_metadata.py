from __future__ import annotations

from ai_stock_sentinel.data_sources.symbol_metadata import (
    TPEX_MAINBOARD_DAILY_QUOTES_URL,
    TWSE_STOCK_DAY_ALL_URL,
    SymbolMetadataResolver,
    detect_market,
    strip_symbol_suffix,
)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_symbol_metadata_resolver_reads_twse_name_and_caches_market_rows() -> None:
    calls: list[str] = []

    def fake_get(url: str, **_kwargs):
        calls.append(url)
        assert url == TWSE_STOCK_DAY_ALL_URL
        return FakeResponse([
            {"Code": "2330", "Name": "台積電"},
            {"Code": "2454", "Name": "聯發科"},
        ])

    resolver = SymbolMetadataResolver(request_get=fake_get, clock=lambda: 1000)

    assert resolver.resolve("2330.TW").name == "台積電"
    assert resolver.resolve("2454.TW").name == "聯發科"
    assert calls == [TWSE_STOCK_DAY_ALL_URL]


def test_symbol_metadata_resolver_reads_tpex_name() -> None:
    def fake_get(url: str, **_kwargs):
        assert url == TPEX_MAINBOARD_DAILY_QUOTES_URL
        return FakeResponse([
            {"SecuritiesCompanyCode": "6488", "SecuritiesCompanyName": "環球晶"},
        ])

    resolver = SymbolMetadataResolver(request_get=fake_get)

    metadata = resolver.resolve("6488.TWO")

    assert metadata.name == "環球晶"
    assert metadata.market == "TWO"


def test_symbol_metadata_resolver_returns_none_when_unknown() -> None:
    resolver = SymbolMetadataResolver(request_get=lambda *_args, **_kwargs: FakeResponse([]))

    metadata = resolver.resolve("9999.TW")

    assert metadata.symbol == "9999.TW"
    assert metadata.name is None


def test_symbol_metadata_provider_failure_uses_short_market_cache() -> None:
    now = 1000.0
    calls = 0

    def fake_get(_url: str, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider temporarily unavailable")
        return FakeResponse([{"Code": "2330", "Name": "台積電"}])

    resolver = SymbolMetadataResolver(request_get=fake_get, clock=lambda: now)

    assert resolver.resolve("2330.TW").name is None
    assert resolver.resolve("2454.TW").name is None
    assert calls == 1

    now += 301
    assert resolver.resolve("2330.TW").name == "台積電"
    assert calls == 2


def test_symbol_metadata_symbol_helpers() -> None:
    assert strip_symbol_suffix("2330.tw") == "2330"
    assert detect_market("6488.TWO") == "TWO"
    assert detect_market("2330.TW") == "TW"
