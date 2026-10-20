from datetime import date

from sqlalchemy.orm import Session

from ai_stock_sentinel.daily_radar.raw_data import _apply_institutional_payloads
from ai_stock_sentinel.db.models import StockRawData
from test_forward_validation_memory import engine_with_tables


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
