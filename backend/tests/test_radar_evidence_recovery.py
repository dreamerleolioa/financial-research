from datetime import date

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.institutional_evidence import OfficialInstitutionalEvidenceProvider
from ai_stock_sentinel.daily_radar.raw_data import _apply_institutional_payloads
from ai_stock_sentinel.db.models import StockRawData
from test_daily_radar_institutional_evidence import _Response
from test_forward_validation_memory import engine_with_tables


def test_one_short_stock_does_not_shrink_the_market_or_other_stock_history():
    dates = {"20261014", "20261013", "20261012", "20261008", "20261007"}

    def get(url, *, params, timeout):
        day = params["date"]
        rows = []
        if day in dates:
            for symbol in (["2330", "2323"] if day >= "20261012" else ["2330"]):
                row = ["0"] * 19
                row[0] = symbol
                rows.append(row)
        return _Response({"stat": "OK", "data": rows})

    result = OfficialInstitutionalEvidenceProvider(request_get=get).fetch(
        ["2330.TW", "2323.TW"], run_date=date(2026, 10, 14))
    assert result.errors == []
    assert len(result.payloads_by_symbol["2330.TW"]["recent_source_dates"]) == 5
    short = result.payloads_by_symbol["2323.TW"]
    assert len(short["recent_source_dates"]) == 3
    assert short["history_coverage"]["missing_dates"] == ["2026-10-07", "2026-10-08"]


def test_ohlcv_refresh_preserves_same_date_evidence_but_keeps_canonical_fields():
    engine = engine_with_tables()
    day = date(2026, 10, 14)
    history = {"rows": [{"source_date": str(day), "source_provider": "official_twse_tpex"}]}
    coverage = {"as_of_date": str(day), "status": "complete"}
    with Session(engine) as session:
        row = StockRawData(symbol="2330.TW", record_date=day, raw_data_is_final=True,
                           institutional={"institutional_daily_flow": history, "history_coverage": coverage,
                                          "institutional_flow": {"same_day_rank": 99}})
        session.add(row)
        session.flush()
        _apply_institutional_payloads(session, run_date=day, symbols=[row.symbol],
            institutional_payloads_by_symbol={row.symbol: {"institutional_flow": {"same_day_rank": 1}}})
        assert row.institutional["institutional_daily_flow"] == history
        assert row.institutional["history_coverage"] == coverage
        assert row.institutional["institutional_flow"]["same_day_rank"] == 1
    engine.dispose()


def test_verified_archive_market_dates_avoid_external_requests_for_short_stock():
    days = [date(2026, 10, d) for d in (7, 8, 12, 13, 14)]
    value = {"foreign": 1, "trust": 2, "dealer": 3, "total": 6}
    def forbidden(*args, **kwargs):
        raise AssertionError("verified archive is already complete")
    result = OfficialInstitutionalEvidenceProvider(request_get=forbidden).fetch(
        ["2330.TW", "2323.TW"], run_date=date(2026, 10, 14),
        cached_market_dates={"TWSE": days}, cached_daily_rows={
            "2330.TW": {day: value for day in days}, "2323.TW": {day: value for day in days[-3:]}})
    assert result.errors == []
    assert result.payloads_by_symbol["2323.TW"]["history_coverage"]["available_day_count"] == 3


def test_future_evidence_is_not_preserved_by_ohlcv_refresh():
    engine = engine_with_tables()
    day = date(2026, 10, 14)
    with Session(engine) as session:
        row = StockRawData(symbol="2330.TW", record_date=day, raw_data_is_final=True,
            institutional={"history_coverage": {"as_of_date": str(day), "status": "complete"},
                           "institutional_daily_flow": {"rows": [{"source_date": "2026-10-15"}]}})
        session.add(row)
        session.flush()
        _apply_institutional_payloads(session, run_date=day, symbols=[row.symbol],
                                     institutional_payloads_by_symbol={row.symbol: {"same_day_rank": 1}})
        assert "institutional_daily_flow" not in row.institutional
        assert "history_coverage" not in row.institutional
    engine.dispose()


def test_evidence_ohlcv_refresh_and_revalidation_survive_committed_replays():
    """Replay the repair sequence across real commits, with deterministic sources."""
    from sqlalchemy import select
    from ai_stock_sentinel.daily_radar.institutional_evidence import cached_daily_rows_from_raw_rows
    from ai_stock_sentinel.daily_radar.pipeline_support import _ai_evidence_missing_by_lane, _merge_institutional_evidence
    from ai_stock_sentinel.daily_radar.research_validation import run_research_validation
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
    from test_daily_radar_forward_validation import _add_run, _add_candidate
    from test_daily_radar_research_validation import price
    days = [date(2026, 10, d) for d in (7, 8, 12, 13, 14)]
    day = days[-1]
    values = {"foreign": 1, "trust": 2, "dealer": 3, "total": 6}
    def forbidden(*args, **kwargs):
        raise AssertionError("verified archive must avoid network requests")
    provider = OfficialInstitutionalEvidenceProvider(request_get=forbidden)
    initial = provider.fetch(["2330.TW", "2323.TW"], run_date=day,
        cached_market_dates={"TWSE": days}, cached_daily_rows={
            "2330.TW": {d: values for d in days}, "2323.TW": {d: values for d in days[-3:]}})
    engine = engine_with_tables()
    with Session(engine) as session:
        session.add_all(StockRawData(symbol=s, record_date=day, raw_data_is_final=True)
                        for s in initial.payloads_by_symbol)
        session.flush()
        _apply_institutional_payloads(session, run_date=day, symbols=initial.payloads_by_symbol,
                                     institutional_payloads_by_symbol=initial.payloads_by_symbol)
        session.commit()
        canonical = {s: {"institutional_flow": {"same_day_rank": 1}} for s in initial.payloads_by_symbol}
        _apply_institutional_payloads(session, run_date=day, symbols=canonical,
                                     institutional_payloads_by_symbol=canonical)
        session.commit()
        rows = session.scalars(select(StockRawData)).all()
        rebuilt = provider.fetch(list(canonical), run_date=day, cached_market_dates={"TWSE": days},
                                 cached_daily_rows=cached_daily_rows_from_raw_rows(rows))
        assert rebuilt.errors == []
        _apply_institutional_payloads(session, run_date=day, symbols=canonical,
            institutional_payloads_by_symbol={s: _merge_institutional_evidence(payload, canonical[s])
                                              for s, payload in rebuilt.payloads_by_symbol.items()})
        session.commit()
        rows = session.scalars(select(StockRawData)).all()
        assert _ai_evidence_missing_by_lane(rows)["institutional"] == ["2323.TW"]
        _add_candidate(session, _add_run(session, run_date=day))
        session.commit()
        class Prices:
            def fetch(self, symbols, **kwargs):
                return {s: [price("2026-10-14"), price("2026-10-15"), price("2026-10-16")] for s in symbols}
        request = DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[2])
        first = run_research_validation(session, request, as_of_date=date(2026, 10, 16), price_provider=Prices())
        assert first["validated_count"] == 1
        session.commit()
        repeated = run_research_validation(session, request, as_of_date=date(2026, 10, 16), price_provider=Prices())
        assert repeated["records_written"] == 0
        assert repeated["retryable_skipped_count"] == 0
    engine.dispose()
