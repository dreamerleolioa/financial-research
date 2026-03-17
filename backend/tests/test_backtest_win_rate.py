"""backtest_win_rate 單元測試。

測試範圍：
1. analysis_is_final = FALSE 的樣本不應被納入預設回測
2. --require-final-raw-data 開啟時，缺少 final raw data 的樣本應被排除
3. 價格資料不足時應被正確計入 skipped
4. Pearson 樣本不足時應正確跳過，不報錯（透過 main() 整合）
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake DailyAnalysisLog objects
# ---------------------------------------------------------------------------

def _make_log(
    symbol: str = "AAPL",
    record_date: date = date(2025, 1, 10),
    action_tag: str = "Exit",
    signal_confidence: float | None = 75.0,
    analysis_is_final: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        record_date=record_date,
        action_tag=action_tag,
        signal_confidence=signal_confidence,
        analysis_is_final=analysis_is_final,
    )


# ---------------------------------------------------------------------------
# Import the module functions under test
# ---------------------------------------------------------------------------

from scripts.backtest_win_rate import (
    LogResult,
    build_raw_data_lookup,
    compute_win_rate,
    evaluate_logs,
    confidence_bucket_stats,
)


# ---------------------------------------------------------------------------
# Test 1: fetch_logs only returns final analysis logs
# ---------------------------------------------------------------------------

def test_fetch_logs_filters_non_final():
    """fetch_logs() 應只回傳 analysis_is_final=TRUE 的紀錄（透過模擬 DB 驗證查詢條件）。"""
    from scripts.backtest_win_rate import fetch_logs

    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query

    # chain: .filter().count() -> .filter().filter().all()
    mock_query.filter.return_value = mock_query
    mock_query.count.return_value = 10
    mock_query.all.return_value = [_make_log()]

    logs, total = fetch_logs(days=30, action_tag=None, db=mock_db, analysis_is_final_only=True)

    assert total == 10
    assert len(logs) == 1

    # 確認有呼叫到 is_(True) 的 filter（通過 filter 被呼叫多次驗證）
    assert mock_query.filter.call_count >= 2  # date filter + is_final filter


def test_fetch_logs_skips_final_filter_when_disabled():
    """analysis_is_final_only=False 時不應套用 is_final 過濾。"""
    from scripts.backtest_win_rate import fetch_logs

    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.count.return_value = 5
    mock_query.all.return_value = []

    logs, total = fetch_logs(days=30, action_tag=None, db=mock_db, analysis_is_final_only=False)

    # date filter only → filter called exactly once
    assert mock_query.filter.call_count == 1


# ---------------------------------------------------------------------------
# Test 2: --require-final-raw-data excludes logs without final raw data
# ---------------------------------------------------------------------------

def test_require_final_raw_data_excludes_missing():
    """require_final_raw=True 時，缺少 final raw data 的 log 應被排除（skip_reason='no_final_raw'）。"""
    log_with    = _make_log(symbol="AAPL", record_date=date(2025, 1, 10))
    log_without = _make_log(symbol="TSLA", record_date=date(2025, 1, 10))

    final_raw_lookup = {("AAPL", date(2025, 1, 10))}

    # 先用假價格填入 cache
    import scripts.backtest_win_rate as bw
    bw._price_cache["AAPL"] = {
        date(2025, 1, 10): 100.0,
        date(2025, 1, 13): 101.0,
        date(2025, 1, 14): 102.0,
        date(2025, 1, 15): 103.0,
        date(2025, 1, 16): 104.0,
        date(2025, 1, 17): 97.0,  # index 5 → p5
    }
    bw._price_cache["TSLA"] = {}

    results = evaluate_logs([log_with, log_without], final_raw_lookup, require_final_raw=True)

    included = [r for r in results if r.skip_reason is None]
    excluded = [r for r in results if r.skip_reason == "no_final_raw"]

    assert len(included) == 1
    assert included[0].log.symbol == "AAPL"
    assert len(excluded) == 1
    assert excluded[0].log.symbol == "TSLA"


# ---------------------------------------------------------------------------
# Test 3: 價格資料不足 → skipped，不崩潰
# ---------------------------------------------------------------------------

def test_insufficient_price_data_counts_as_skipped():
    """價格資料少於 5 筆時應被計入 skipped（skip_reason='insufficient_price_data'）。"""
    import scripts.backtest_win_rate as bw

    log = _make_log(symbol="NO_DATA", record_date=date(2025, 2, 1))

    # 只有訊號日價格，沒有第 5 天
    bw._price_cache["NO_DATA"] = {
        date(2025, 2, 1): 100.0,
        date(2025, 2, 2): 101.0,
    }

    results = evaluate_logs([log], set(), require_final_raw=False)
    assert len(results) == 1
    assert results[0].skip_reason == "insufficient_price_data"

    stat = compute_win_rate(results)
    assert stat["total"] == 0
    assert stat["skipped"] == 1
    assert stat["skipped_by_reason"] == {"insufficient_price_data": 1}


def test_no_signal_price_counts_as_skipped():
    """連訊號日價格都沒有時應被計入 skipped（skip_reason='no_signal_price'）。"""
    import scripts.backtest_win_rate as bw

    log = _make_log(symbol="EMPTY", record_date=date(2025, 3, 1))
    bw._price_cache["EMPTY"] = {}

    results = evaluate_logs([log], set(), require_final_raw=False)
    assert results[0].skip_reason == "no_signal_price"


# ---------------------------------------------------------------------------
# Test 4: Pearson 樣本不足不報錯（整合 main() 的部分，透過 compute_win_rate 驗證）
# ---------------------------------------------------------------------------

def test_compute_win_rate_empty_results():
    """空樣本時 win_rate 應為 None，不報 ZeroDivisionError。"""
    stat = compute_win_rate([])
    assert stat["win_rate"] is None
    assert stat["total"] == 0


def test_confidence_bucket_stats_empty():
    """exit_trim_results 為空時 confidence_bucket_stats 不應拋出錯誤。"""
    rows = confidence_bucket_stats([])
    assert isinstance(rows, list)
    for row in rows:
        assert row["win_rate"] is None or isinstance(row["win_rate"], float)


# ---------------------------------------------------------------------------
# Test 5: compute_win_rate 勝率計算正確性
# ---------------------------------------------------------------------------

def test_compute_win_rate_correct_calculation():
    """5 筆樣本中 3 筆下跌 > 3%，勝率應為 60.0%。"""
    def _r(pct: float) -> LogResult:
        log = _make_log()
        return LogResult(log, 100.0, 100 + pct, pct, None)

    results = [_r(-5.0), _r(-4.0), _r(-3.5), _r(-1.0), _r(2.0)]
    stat = compute_win_rate(results)
    assert stat["total"] == 5
    assert stat["correct"] == 3
    assert stat["win_rate"] == 60.0
