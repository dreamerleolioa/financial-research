"""ETF tracking remains independent of the stock research eligibility boundary."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ai_stock_sentinel import main
from ai_stock_sentinel.analysis.schemas import AnalyzeRequest
from ai_stock_sentinel.daily_radar.forward_validation import forward_validation_candidates_from_runs
from ai_stock_sentinel.daily_radar.research_validation import evaluate_research_window
from ai_stock_sentinel.taiwan_symbols import is_supported_taiwan_symbol
from test_forward_validation_memory import engine_with_tables
from test_daily_radar_forward_validation import _add_candidate, _add_run


@pytest.mark.parametrize("symbol", ["0050.TW", "00637L.TW", "00713.TW", "00985A.TW", "006201.TWO", "2330A.TW"])
def test_non_stock_instruments_never_enter_analysis(symbol):
    assert not is_supported_taiwan_symbol(symbol)
    with pytest.raises(ValidationError):
        AnalyzeRequest(symbol=symbol)


@pytest.mark.parametrize("symbol", ["2330.TW", "6488.TWO", "9105.TW", " 2330.tw "])
def test_supported_stocks_are_normalized(symbol):
    assert AnalyzeRequest(symbol=symbol).symbol == symbol.strip().upper()


def test_cli_rejects_etf_before_building_graph(capsys):
    with patch.object(main, "build_graph", MagicMock()) as graph, patch("sys.argv", ["main", "--symbol", "0050.TW"]):
        with pytest.raises(SystemExit) as error:
            main.main()
        assert error.value.code == 2
        graph.assert_not_called()
    assert "不支援 ETF" in capsys.readouterr().err


def test_historical_etf_candidates_are_not_loaded_for_either_validation_basis():
    engine = engine_with_tables()
    with Session(engine) as session:
        run = _add_run(session)
        _add_candidate(session, run, symbol="0050.TW")
        _add_candidate(session, run, symbol="2330.TW")
        session.commit()
        assert [c["symbol"] for c in forward_validation_candidates_from_runs(session, market="TW")] == ["2330.TW"]
    engine.dispose()


def test_next_open_rejects_stale_signal_before_requesting_missing_benchmark():
    result = evaluate_research_window(
        {"candidate_id": 1, "symbol": "2330.TW", "record_date": "2026-06-19", "data_dates": {"ohlcv": "2026-06-18"}},
        price_series=[], benchmark_prices=[], window_days=5,
        as_of_date=date(2026, 10, 14), benchmark_symbol="TAIEX",
    )
    assert result["skip_reason"] == "stale_candidate_price"


def test_retryable_window_is_resumed_outside_the_default_lookback():
    from ai_stock_sentinel.daily_radar.research_validation import RESEARCH_VALIDATION_VERSION, run_research_validation
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
    from ai_stock_sentinel.db.models import DailyRadarForwardValidationResult
    from test_daily_radar_research_validation import price
    engine = engine_with_tables()
    with Session(engine) as session:
        candidate = _add_candidate(session, _add_run(session, run_date=date(2026, 1, 2)))
        session.add(DailyRadarForwardValidationResult(candidate_id=candidate.id, window_days=1,
            validation_version=RESEARCH_VALIDATION_VERSION, status="skipped", signal_date=date(2026, 1, 2),
            benchmark_symbol="TAIEX", evaluation_as_of_date=date(2026, 1, 5),
            skip_reason="missing_future_price", outcome={}))
        session.commit()
        class Provider:
            calls = 0
            def fetch(self, symbols, **kwargs):
                self.calls += 1
                return {symbol: [price("2026-01-02"), price("2026-01-05")] for symbol in symbols}
        provider = Provider()
        request = DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[1])
        result = run_research_validation(session, request, as_of_date=date(2026, 10, 14), price_provider=provider)
        assert result["validated_count"] == 1
        assert result["report"]["resumed_candidate_count"] == 1
        session.commit()
        result = run_research_validation(session, request, as_of_date=date(2026, 10, 14), price_provider=provider)
        assert result["records_written"] == 0
        assert provider.calls == 2
    engine.dispose()


def test_prepared_calendar_hole_does_not_drop_a_real_benchmark_session(monkeypatch):
    from ai_stock_sentinel.daily_radar import research_validation as research
    from ai_stock_sentinel.daily_radar.schemas import DailyRadarForwardValidationRunRequest
    from test_daily_radar_research_validation import price
    engine = engine_with_tables()
    with Session(engine) as session:
        _add_candidate(session, _add_run(session, run_date=date(2026, 6, 5)))
        session.commit()
        class Provider:
            def fetch(self, symbols, **kwargs):
                return {s: [price("2026-06-05"), price("2026-06-08"), price("2026-06-09")] for s in symbols}
        monkeypatch.setattr(research, "load_benchmark_prices_from_prepared_market_context",
            lambda *a, **k: [price("2026-06-05"), price("2026-06-09")])
        result = research.run_research_validation(session,
            DailyRadarForwardValidationRunRequest(return_basis="next_open", windows=[2]),
            as_of_date=date(2026, 6, 9), price_provider=Provider())
        assert result["validated_count"] == 1
    engine.dispose()


def test_general_validation_excludes_legacy_etf_samples_and_does_not_capture_new_ones():
    from ai_stock_sentinel.analysis.calibration import (
        CONFIDENCE_CONFIG_VERSION, STRATEGY_VERSION, general_validation_samples,
        capture_general_analysis_calibration_sample,
    )
    from ai_stock_sentinel.db.models import AnalysisCalibrationSample
    from ai_stock_sentinel.db.session import Base
    engine = engine_with_tables()
    Base.metadata.create_all(engine, tables=[AnalysisCalibrationSample.__table__])
    with Session(engine) as session:
        for symbol in ("0050.TW", "2330.TW"):
            session.add(AnalysisCalibrationSample(symbol=symbol, record_date=date(2026, 6, 5),
                analysis_type="general", market="TW", benchmark_symbol="TAIEX", analysis_is_final=True,
                strategy_version=STRATEGY_VERSION, confidence_config_version=CONFIDENCE_CONFIG_VERSION,
                input_hash="a" * 64, replay_input={}, output_snapshot={}))
        session.commit()
        assert [row["symbol"] for row in general_validation_samples(session)] == ["2330.TW"]
        assert capture_general_analysis_calibration_sample(session, symbol="00713.TW",
            record_date=date(2026, 6, 5), result={}, is_final=True) is None
    engine.dispose()
