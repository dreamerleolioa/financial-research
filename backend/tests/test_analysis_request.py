from ai_stock_sentinel.analysis.schemas import AnalyzeRequest


def test_analysis_persists_by_default() -> None:
    assert AnalyzeRequest(symbol="2330.TW").should_persist_result is True


def test_persist_result_controls_cache_and_history_writes() -> None:
    assert AnalyzeRequest(symbol="2330.TW", persist_result=False).should_persist_result is False
    assert AnalyzeRequest(symbol="2330.TW", persist_result=True).should_persist_result is True


def test_legacy_skip_ai_maps_to_non_persistent_request() -> None:
    assert AnalyzeRequest(symbol="2330.TW", skip_ai=True).should_persist_result is False


def test_explicit_persist_result_wins_over_legacy_flag() -> None:
    request = AnalyzeRequest(symbol="2330.TW", persist_result=True, skip_ai=True)
    assert request.should_persist_result is True
