#!/usr/bin/env python
# backend/scripts/backtest_win_rate.py
"""
勝率回測腳本

用法：
    python scripts/backtest_win_rate.py --days 90
    python scripts/backtest_win_rate.py --days 90 --action-tag Exit
    python scripts/backtest_win_rate.py --days 90 --require-final-raw-data
    python scripts/backtest_win_rate.py --days 90 --output-json result.json
    python scripts/backtest_win_rate.py --mode new-position --days 90
    python scripts/backtest_win_rate.py --mode new-position --hold-days 10

定義：
    position 模式（預設）：勝率 = Exit/Trim 訊號發出後 5 個交易日內，股價下跌 > 3% 的比率
    new-position 模式：勝率 = 新倉建議後第 N 個交易日收盤價，漲幅 > +3% 的比率

回測口徑：
    - 主口徑為「分析訊號準確率」，非純價格統計
    - 預設只納入 analysis_is_final = TRUE 的樣本
    - 可選 --require-final-raw-data 進一步限制只納入同時有 final raw data 的樣本（position 模式）
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from typing import NamedTuple

import yfinance as yf
from scipy.stats import pearsonr
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyAnalysisLog, StockRawData
from ai_stock_sentinel.db.session import _get_session_local


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class FetchSummary(NamedTuple):
    total_logs: int
    final_analysis_included: int
    excluded_not_final: int
    excluded_no_price: int
    excluded_no_final_raw: int  # only relevant when --require-final-raw-data
    has_final_raw: int
    no_final_raw: int


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def fetch_logs(
    days: int,
    action_tag: str | None,
    db: Session,
    *,
    analysis_is_final_only: bool = True,
    symbols: list[str] | None = None,
    min_confidence: float | None = None,
) -> tuple[list[DailyAnalysisLog], int]:
    """從 DB 讀取指定期間的診斷 log。

    Returns:
        (filtered_logs, total_count_before_filter)
    """
    since = date.today() - timedelta(days=days)
    q = db.query(DailyAnalysisLog).filter(DailyAnalysisLog.record_date >= since)

    total_count = q.count()

    if analysis_is_final_only:
        q = q.filter(DailyAnalysisLog.analysis_is_final.is_(True))

    if action_tag:
        q = q.filter(DailyAnalysisLog.action_tag == action_tag)

    if symbols:
        q = q.filter(DailyAnalysisLog.symbol.in_(symbols))

    if min_confidence is not None:
        q = q.filter(DailyAnalysisLog.signal_confidence >= min_confidence)

    return q.all(), total_count


def fetch_new_position_logs(
    days: int,
    db: Session,
    *,
    analysis_is_final_only: bool = True,
    min_confidence: float | None = None,
) -> tuple[list[DailyAnalysisLog], int]:
    """從 DB 讀取新倉策略 log（strategy_type IN short_term/mid_term）。

    strategy_type 存於 indicators JSONB 欄位中。

    Returns:
        (filtered_logs, total_count_before_filter)
    """
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    since = date.today() - timedelta(days=days)
    q = db.query(DailyAnalysisLog).filter(DailyAnalysisLog.record_date >= since)
    total_count = q.count()

    if analysis_is_final_only:
        q = q.filter(DailyAnalysisLog.analysis_is_final.is_(True))

    if min_confidence is not None:
        q = q.filter(DailyAnalysisLog.signal_confidence >= min_confidence)

    # 篩選 strategy_type IN ('short_term', 'mid_term')（存於 indicators JSONB）
    q = q.filter(
        DailyAnalysisLog.indicators["strategy_type"].astext.in_(["short_term", "mid_term"])
    )

    return q.all(), total_count


def build_raw_data_lookup(
    logs: list[DailyAnalysisLog], db: Session
) -> set[tuple[str, date]]:
    """回傳所有存在 raw_data_is_final = TRUE 的 (symbol, record_date) 集合。"""
    if not logs:
        return set()

    pairs: list[tuple[str, date]] = [(log.symbol, log.record_date) for log in logs]

    # 批次查詢，避免 N+1
    rows = (
        db.query(StockRawData.symbol, StockRawData.record_date)
        .filter(StockRawData.raw_data_is_final.is_(True))
        .filter(
            StockRawData.record_date.in_([p[1] for p in pairs])
        )
        .all()
    )
    return {(row.symbol, row.record_date) for row in rows}


# ---------------------------------------------------------------------------
# Price helpers (with in-memory cache)
# ---------------------------------------------------------------------------

_price_cache: dict[str, dict] = {}  # symbol -> {date: price}


def _ensure_price_history(symbol: str, start: date, end: date) -> dict[date, float]:
    """批次抓取並快取某 symbol 的收盤價歷史（dict: trade_date -> close_price）。"""
    if symbol not in _price_cache:
        _price_cache[symbol] = {}

    cache = _price_cache[symbol]
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=start.isoformat(), end=end.isoformat())

    for ts, row in hist.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        cache[d] = float(row["Close"])

    return cache


def _get_nth_trading_close(symbol: str, from_date: date, n: int) -> float | None:
    """取得 from_date 起第 n 個交易日（0-indexed）的收盤價。"""
    cache = _price_cache.get(symbol, {})
    sorted_dates = sorted(d for d in cache if d >= from_date)
    if len(sorted_dates) <= n:
        return None
    return cache[sorted_dates[n]]


def prefetch_prices(logs: list[DailyAnalysisLog]) -> dict[str, list[str]]:
    """為所有 log 批次預抓價格。回傳 {symbol: [error_reason]} 的錯誤字典。"""
    by_symbol: dict[str, list[date]] = defaultdict(list)
    for log in logs:
        by_symbol[log.symbol].append(log.record_date)

    errors: dict[str, list[str]] = {}

    for symbol, dates in by_symbol.items():
        start = min(dates)
        end = max(dates) + timedelta(days=15)  # 多取 15 天確保涵蓋 5 個交易日
        try:
            _ensure_price_history(symbol, start, end)
        except Exception as exc:
            errors[symbol] = [str(exc)]

    return errors


# ---------------------------------------------------------------------------
# Win-rate computation
# ---------------------------------------------------------------------------

class LogResult(NamedTuple):
    log: DailyAnalysisLog
    p0: float | None
    p5: float | None
    pct_change: float | None
    skip_reason: str | None  # None = included


def evaluate_logs(
    logs: list[DailyAnalysisLog],
    final_raw_lookup: set[tuple[str, date]],
    require_final_raw: bool,
) -> list[LogResult]:
    results = []
    for log in logs:
        # raw data check
        has_final_raw = (log.symbol, log.record_date) in final_raw_lookup
        if require_final_raw and not has_final_raw:
            results.append(LogResult(log, None, None, None, "no_final_raw"))
            continue

        p0 = _get_nth_trading_close(log.symbol, log.record_date, 0)
        p5 = _get_nth_trading_close(log.symbol, log.record_date, 5)

        if p0 is None:
            results.append(LogResult(log, None, None, None, "no_signal_price"))
            continue
        if p5 is None:
            results.append(LogResult(log, p0, None, None, "insufficient_price_data"))
            continue

        pct = (p5 - p0) / p0 * 100
        results.append(LogResult(log, p0, p5, pct, None))

    return results


def compute_win_rate(
    results: list[LogResult], threshold_pct: float = -3.0
) -> dict:
    included = [r for r in results if r.skip_reason is None]
    correct = sum(1 for r in included if r.pct_change <= threshold_pct)
    skipped_by_reason: dict[str, int] = defaultdict(int)
    for r in results:
        if r.skip_reason:
            skipped_by_reason[r.skip_reason] += 1

    return {
        "total":            len(included),
        "correct":          correct,
        "skipped":          len(results) - len(included),
        "skipped_by_reason": dict(skipped_by_reason),
        "win_rate":         round(correct / len(included) * 100, 1) if included else None,
    }


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

CONFIDENCE_BUCKETS = [
    ("<60",  0,    60),
    ("60-70", 60,  70),
    ("70-80", 70,  80),
    ("80+",   80, 200),
]


def confidence_bucket_stats(results: list[LogResult], threshold_pct: float = -3.0) -> list[dict]:
    rows = []
    for label, lo, hi in CONFIDENCE_BUCKETS:
        bucket = [
            r for r in results
            if r.skip_reason is None
            and r.log.signal_confidence is not None
            and lo <= float(r.log.signal_confidence) < hi
        ]
        correct = sum(1 for r in bucket if r.pct_change <= threshold_pct)
        rows.append({
            "bucket":   label,
            "n":        len(bucket),
            "correct":  correct,
            "win_rate": round(correct / len(bucket) * 100, 1) if bucket else None,
        })
    return rows


def compute_new_position_stats(
    results: list[LogResult], threshold_pct: float = 3.0
) -> dict:
    """新倉策略勝率統計。

    勝：漲幅 > +threshold_pct
    敗：漲幅 < -threshold_pct
    平手：漲幅介於 -threshold_pct ~ +threshold_pct（含端點），排出分母外單獨列出
    """
    included = [r for r in results if r.skip_reason is None]
    skipped_by_reason: dict[str, int] = defaultdict(int)
    for r in results:
        if r.skip_reason:
            skipped_by_reason[r.skip_reason] += 1

    wins    = sum(1 for r in included if r.pct_change is not None and r.pct_change > threshold_pct)
    losses  = sum(1 for r in included if r.pct_change is not None and r.pct_change < -threshold_pct)
    neutral = sum(1 for r in included if r.pct_change is not None and -threshold_pct <= r.pct_change <= threshold_pct)
    decisive = wins + losses  # 排除平手後的有效分母

    pct_changes = [r.pct_change for r in included if r.pct_change is not None]
    avg_return = round(sum(pct_changes) / len(pct_changes), 2) if pct_changes else None

    return {
        "total":             len(included),
        "wins":              wins,
        "losses":            losses,
        "neutral":           neutral,
        "decisive":          decisive,
        "skipped":           len(results) - len(included),
        "skipped_by_reason": dict(skipped_by_reason),
        "win_rate":          round(wins / decisive * 100, 1) if decisive else None,
        "draw_rate":         round(neutral / len(included) * 100, 1) if included else None,
        "loss_rate":         round(losses / decisive * 100, 1) if decisive else None,
        "avg_return":        avg_return,
    }


def group_stats(
    results: list[LogResult],
    threshold_pct: float,
    group_key_fn,
) -> list[dict]:
    """按 group_key_fn(log) 分組計算新倉勝率統計。"""
    by_group: dict[str, list[LogResult]] = defaultdict(list)
    for r in results:
        key = group_key_fn(r.log)
        by_group[key].append(r)

    rows = []
    for key, group_results in sorted(by_group.items()):
        stat = compute_new_position_stats(group_results, threshold_pct)
        rows.append({"group": key, **stat})
    return rows


def print_section(title: str) -> None:
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

HOLD_PERIODS = [5, 10, 20]

EVIDENCE_SCORE_BUCKETS = [
    ("<2",  None, 2),
    ("2-3", 2,    4),
    ("4+",  4,    None),
]


def _get_evidence_score_total(log: DailyAnalysisLog) -> float | None:
    """從 indicators JSONB 取出 evidence_scores.total，容錯處理舊資料。"""
    indicators = log.indicators or {}
    evidence_scores = indicators.get("evidence_scores")
    if not isinstance(evidence_scores, dict):
        return None
    total = evidence_scores.get("total")
    if total is None:
        return None
    try:
        return float(total)
    except (TypeError, ValueError):
        return None


def evidence_score_stats(
    results_by_period: dict[int, list[LogResult]],
    threshold_pct: float,
) -> tuple[list[dict], int]:
    """依 evidence_scores.total 分箱，輸出各持有週期的勝率統計。

    evidence_scores 欄位缺失的筆跳過並計入 skip 數。
    Returns:
        (rows, skipped_count)
    """
    first_period_results = next(iter(results_by_period.values()), [])
    skipped = sum(
        1 for r in first_period_results
        if r.skip_reason is None and _get_evidence_score_total(r.log) is None
    )
    rows = []
    for label, lo, hi in EVIDENCE_SCORE_BUCKETS:
        bucket_log_ids = {
            r.log.id
            for r in first_period_results
            if r.skip_reason is None
            and (
                (score := _get_evidence_score_total(r.log)) is not None
                and (lo is None or score >= lo)
                and (hi is None or score < hi)
            )
        }
        period_stats = {}
        for period, period_results in results_by_period.items():
            bucket = [r for r in period_results if r.skip_reason is None and r.log.id in bucket_log_ids]
            stat = compute_new_position_stats(bucket, threshold_pct)
            period_stats[f"hold_{period}d"] = stat
        rows.append({"bucket": label, "by_period": period_stats})
    return rows, skipped


def confidence_bucket_multi_period_stats(
    results_by_period: dict[int, list[LogResult]],
    threshold_pct: float,
) -> tuple[list[dict], int]:
    """依 signal_confidence 分桶，輸出各持有週期的新倉勝率統計。

    signal_confidence 為 None 的筆視為缺失，跳過並計入 skip 數。
    Returns:
        (rows, skipped_count)
    """
    first_period_results = next(iter(results_by_period.values()), [])
    skipped = sum(
        1 for r in first_period_results
        if r.skip_reason is None and r.log.signal_confidence is None
    )

    rows = []
    for label, lo, hi in CONFIDENCE_BUCKETS:
        bucket_log_ids = {
            r.log.id
            for r in first_period_results
            if r.skip_reason is None
            and r.log.signal_confidence is not None
            and lo <= float(r.log.signal_confidence) < hi
        }
        period_stats = {}
        for period, period_results in results_by_period.items():
            bucket = [r for r in period_results if r.skip_reason is None and r.log.id in bucket_log_ids]
            stat = compute_new_position_stats(bucket, threshold_pct)
            period_stats[f"hold_{period}d"] = stat
        rows.append({"bucket": label, "by_period": period_stats})
    return rows, skipped


def multi_period_group_stats(
    results_by_period: dict[int, list[LogResult]],
    threshold_pct: float,
    group_key_fn,
) -> tuple[list[dict], int]:
    """按 group_key_fn 分組，輸出各持有週期的勝率統計。

    group_key_fn 回傳 None 的筆視為欄位缺失，跳過並計入 skip 數。
    Returns:
        (rows, skipped_count)
    """
    first_period_results = next(iter(results_by_period.values()), [])
    skipped = sum(1 for r in first_period_results if r.skip_reason is None and group_key_fn(r.log) is None)
    groups: set[str] = {
        group_key_fn(r.log)
        for r in first_period_results
        if group_key_fn(r.log) is not None
    }

    rows = []
    for key in sorted(groups):
        period_stats = {}
        for period, period_results in results_by_period.items():
            group_results = [r for r in period_results if group_key_fn(r.log) == key]
            stat = compute_new_position_stats(group_results, threshold_pct)
            period_stats[f"hold_{period}d"] = stat
        rows.append({"group": key, "by_period": period_stats})
    return rows, skipped


def print_matrix_table(
    title: str,
    rows: list[dict],
    group_col: str,
    periods: list[int],
) -> None:
    """輸出勝率矩陣表格。"""
    print_section(title)
    header = f"  {'':>14}" + "".join(f"  {p}日勝率" for p in periods)
    print(header)
    for row in rows:
        label = row.get(group_col, "")
        cells = []
        for period in periods:
            stat = row.get("by_period", {}).get(f"hold_{period}d", {})
            win_rate = stat.get("win_rate")
            n = stat.get("total", 0)
            if n < 5:
                cell = f"{'n<5':>8}"
            else:
                cell = f"{(str(win_rate)+'%') if win_rate is not None else '-':>8}"
            cells.append(cell)
        print(f"  [{label:>12}]{''.join(cells)}")


def main_new_position(
    days: int,
    hold_days: int | None,
    output_json: str | None,
    min_confidence: float | None,
) -> None:
    """新倉策略回測主程式。

    hold_days=None 時同時計算 HOLD_PERIODS（5/10/20）；
    hold_days 有值時只計算該週期。
    """
    active_periods = [hold_days] if hold_days is not None else HOLD_PERIODS
    print(f"\n=== 新倉策略回測報告（過去 {days} 天，持有週期：{active_periods}）===")

    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        logs, total_in_range = fetch_new_position_logs(
            days, db,
            analysis_is_final_only=True,
            min_confidence=min_confidence,
        )

    excluded_not_final = total_in_range - len(logs)

    print_section("資料品質統計")
    print(f"  期間內總 log 筆數         : {total_in_range}")
    print(f"  analysis_is_final=TRUE    : {len(logs)}")
    print(f"  排除（未定稿分析）         : {excluded_not_final}")

    if not logs:
        print("\n無符合條件的新倉策略紀錄。")
        return

    # 批次預抓價格（多取 25 天確保涵蓋 20 個交易日）
    by_symbol: dict[str, list[date]] = defaultdict(list)
    for log in logs:
        by_symbol[log.symbol].append(log.record_date)
    fetch_errors: dict[str, list[str]] = {}
    for symbol, dates in by_symbol.items():
        start = min(dates)
        end = max(dates) + timedelta(days=35)
        try:
            _ensure_price_history(symbol, start, end)
        except Exception as exc:
            fetch_errors[symbol] = [str(exc)]
    if fetch_errors:
        print(f"\n  ⚠️  以下 symbol 價格抓取失敗：{list(fetch_errors.keys())}")

    WIN_THRESHOLD = 3.0

    # 分別計算各持有週期的 results
    results_by_period: dict[int, list[LogResult]] = {}
    for period in active_periods:
        period_results = []
        for log in logs:
            p0 = _get_nth_trading_close(log.symbol, log.record_date, 0)
            pn = _get_nth_trading_close(log.symbol, log.record_date, period)
            if p0 is None:
                period_results.append(LogResult(log, None, None, None, "no_signal_price"))
            elif pn is None:
                period_results.append(LogResult(log, p0, None, None, "insufficient_price_data"))
            else:
                pct = (pn - p0) / p0 * 100
                period_results.append(LogResult(log, p0, pn, pct, None))
        results_by_period[period] = period_results

    # 以最短週期計算排除數（p0 缺失即排除，與週期無關）
    base_results = results_by_period[active_periods[0]]
    excluded_no_price = sum(1 for r in base_results if r.skip_reason == "no_signal_price")
    print(f"  排除（無訊號當日價格）     : {excluded_no_price}")

    # 整體統計矩陣
    print_section(f"整體統計（門檻 ±{WIN_THRESHOLD}%）")
    print(f"  {'':>14}" + "".join(f"  {p}日勝率" for p in active_periods))
    cells = []
    for period in active_periods:
        stat = compute_new_position_stats(results_by_period[period], WIN_THRESHOLD)
        n = stat["decisive"]
        wr = stat["win_rate"]
        cells.append(f"{(str(wr)+'%') if wr is not None and n >= 5 else ('n<5' if n < 5 else '-'):>8}")
    print(f"  [{'整體':>12}]{''.join(cells)}")

    # 最短週期詳細統計
    stat_base = compute_new_position_stats(base_results, WIN_THRESHOLD)
    print(f"\n  {active_periods[0]} 日詳細（供參考）：")
    print(f"    納入={stat_base['total']}  勝={stat_base['wins']}  敗={stat_base['losses']}  平={stat_base['neutral']}  有效={stat_base['decisive']}")

    # 按 strategy_type 分組矩陣
    def _strategy_type(log: DailyAnalysisLog) -> str | None:
        return (log.indicators or {}).get("strategy_type") or None

    strategy_type_stats, st_skipped = multi_period_group_stats(results_by_period, WIN_THRESHOLD, _strategy_type)
    print_matrix_table("各 strategy_type 勝率矩陣", strategy_type_stats, "group", active_periods)
    if st_skipped:
        print(f"  ⚠️  {st_skipped} 筆缺少 strategy_type，已跳過")

    # 按 conviction_level 分組矩陣
    def _conviction_level(log: DailyAnalysisLog) -> str | None:
        return (log.indicators or {}).get("conviction_level") or None

    conviction_stats, cv_skipped = multi_period_group_stats(results_by_period, WIN_THRESHOLD, _conviction_level)
    print_matrix_table("各 conviction_level 勝率矩陣", conviction_stats, "group", active_periods)
    if cv_skipped:
        print(f"  ⚠️  {cv_skipped} 筆缺少 conviction_level，已跳過")

    # evidence_scores.total 分箱矩陣
    ev_score_stats, ev_skipped = evidence_score_stats(results_by_period, WIN_THRESHOLD)
    print_matrix_table("evidence_scores.total 分箱勝率矩陣", ev_score_stats, "bucket", active_periods)
    if ev_skipped:
        print(f"  ⚠️  {ev_skipped} 筆缺少 evidence_scores.total，已跳過")

    # signal_confidence 分桶勝率矩陣
    conf_bucket_stats, conf_bucket_skipped = confidence_bucket_multi_period_stats(results_by_period, WIN_THRESHOLD)
    print_matrix_table("signal_confidence 分桶勝率矩陣", conf_bucket_stats, "bucket", active_periods)
    if conf_bucket_skipped:
        print(f"  ⚠️  {conf_bucket_skipped} 筆缺少 signal_confidence，已跳過")

    # 單調性診斷
    first_period_key = f"hold_{active_periods[0]}d"
    bucket_win_rates = [
        row["by_period"][first_period_key].get("win_rate")
        for row in conf_bucket_stats
        if row["by_period"][first_period_key].get("total", 0) >= 5
    ]
    if len(bucket_win_rates) >= 2:
        is_monotone = all(
            bucket_win_rates[i] <= bucket_win_rates[i + 1]
            for i in range(len(bucket_win_rates) - 1)
            if bucket_win_rates[i] is not None and bucket_win_rates[i + 1] is not None
        )
        if not is_monotone:
            print(f"  ⚠️  signal_confidence 分桶勝率不呈單調遞增（{active_periods[0]}日），建議執行維度分析腳本。")
        else:
            print(f"  ✅  signal_confidence 分桶勝率單調遞增（{active_periods[0]}日），分桶校準正常。")

    # Pearson 相關性分析（新倉版，基於最短週期漲幅）
    pearson_base_period = active_periods[0]
    print_section(f"Pearson 相關性分析（新倉版，{pearson_base_period} 日漲幅）")
    pearson_stats: dict = {}
    valid_5d = [r for r in results_by_period[pearson_base_period] if r.skip_reason is None and r.pct_change is not None]

    # signal_confidence vs 5日漲幅
    conf_valid = [r for r in valid_5d if r.log.signal_confidence is not None]
    if len(conf_valid) < 5:
        print(f"  signal_confidence 有效樣本不足（{len(conf_valid)} < 5），跳過。")
    else:
        confidences = [float(r.log.signal_confidence) for r in conf_valid]
        returns = [r.pct_change for r in conf_valid]
        corr, pval = pearsonr(confidences, returns)
        pearson_stats["signal_confidence"] = {"r": round(corr, 3), "p": round(pval, 3), "n": len(conf_valid)}
        print(f"  signal_confidence vs 5日漲幅：r={corr:.3f}  p={pval:.3f}  n={len(conf_valid)}")
        if abs(corr) < 0.2:
            print("  ⚠️  相關性偏低（|r|<0.2），signal_confidence 對 5 日漲幅預測力不足。")
        else:
            print("  ✅  相關性合理。")

    # evidence_scores.total vs 5日漲幅
    ev_valid = [r for r in valid_5d if _get_evidence_score_total(r.log) is not None]
    if len(ev_valid) < 5:
        print(f"  evidence_scores.total 有效樣本不足（{len(ev_valid)} < 5），跳過。")
    else:
        ev_scores = [_get_evidence_score_total(r.log) for r in ev_valid]
        returns_ev = [r.pct_change for r in ev_valid]
        corr_ev, pval_ev = pearsonr(ev_scores, returns_ev)
        pearson_stats["evidence_total"] = {"r": round(corr_ev, 3), "p": round(pval_ev, 3), "n": len(ev_valid)}
        print(f"  evidence_scores.total vs 5日漲幅：r={corr_ev:.3f}  p={pval_ev:.3f}  n={len(ev_valid)}")
        if abs(corr_ev) < 0.2:
            print("  ⚠️  相關性偏低（|r|<0.2），evidence_scores.total 對 5 日漲幅預測力不足。")
        else:
            print("  ✅  相關性合理。")

    print("\n⚠️  注意：校準結果需人工審核後才可調整策略邏輯。")
    print("  回測解讀準則：勝率>60%=有預測價值；50-60%=邊際有效；<50%=需審核策略邏輯")
    print("  分箱 n<5 時數據不足，分箱 n>=30 可初步得出結論。")

    if output_json:
        overall_by_period = {
            f"hold_{p}d": compute_new_position_stats(results_by_period[p], WIN_THRESHOLD)
            for p in active_periods
        }
        # F6-5: multi_period_matrix = {strategy_type: {hold_days: win_rate}}
        multi_period_matrix: dict = {}
        for row in strategy_type_stats:
            key = row["group"]
            multi_period_matrix[key] = {
                p: row["by_period"].get(f"hold_{p}d", {}).get("win_rate")
                for p in active_periods
            }
        payload = {
            "mode":              "new-position",
            "days":              days,
            "hold_days":         hold_days,
            "hold_periods":      active_periods,
            "win_threshold_pct": WIN_THRESHOLD,
            "data_quality": {
                "total_logs":           total_in_range,
                "final_analysis_count": len(logs),
                "excluded_not_final":   excluded_not_final,
                "excluded_no_price":    excluded_no_price,
            },
            "overall_by_period":         overall_by_period,
            "strategy_type_stats":       strategy_type_stats,
            "conviction_stats":          conviction_stats,
            "evidence_score_stats":      ev_score_stats,
            "confidence_bucket_stats":   conf_bucket_stats,
            "multi_period_matrix":       multi_period_matrix,
            "pearson":                   pearson_stats,
        }
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n結果已寫入 {output_json}")


def main(
    days: int,
    action_tag: str | None,
    require_final_raw: bool,
    output_json: str | None,
    min_confidence: float | None,
) -> None:
    print(f"\n=== 勝率回測報告（過去 {days} 天）===")
    if require_final_raw:
        print("  模式：analysis_is_final + raw_data_is_final（嚴格模式）")
    else:
        print("  模式：analysis_is_final only（預設模式）")

    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        logs, total_in_range = fetch_logs(
            days, action_tag, db,
            analysis_is_final_only=True,
            min_confidence=min_confidence,
        )
        final_raw_lookup = build_raw_data_lookup(logs, db)

    excluded_not_final = total_in_range - len(logs)

    # 資料品質概覽
    print_section("資料品質統計")
    print(f"  期間內總 log 筆數         : {total_in_range}")
    print(f"  analysis_is_final=TRUE    : {len(logs)}")
    print(f"  排除（未定稿分析）         : {excluded_not_final}")

    has_raw = sum(1 for log in logs if (log.symbol, log.record_date) in final_raw_lookup)
    no_raw  = len(logs) - has_raw
    raw_cov = round(has_raw / len(logs) * 100, 1) if logs else 0
    print(f"  具有 final raw data 樣本數 : {has_raw}  ({raw_cov}%)")
    print(f"  缺少 final raw data 樣本數 : {no_raw}")
    if no_raw > 0 and not require_final_raw:
        print("  ⚠️  部分樣本缺少 final raw data，建議用 --require-final-raw-data 驗證")

    if not logs:
        print("\n無符合條件的診斷紀錄。")
        return

    # 批次預抓價格
    fetch_errors = prefetch_prices(logs)
    if fetch_errors:
        print(f"\n  ⚠️  以下 symbol 價格抓取失敗：{list(fetch_errors.keys())}")

    # 評估每筆 log
    all_results = evaluate_logs(logs, final_raw_lookup, require_final_raw)
    excluded_no_price     = sum(1 for r in all_results if r.skip_reason in ("no_signal_price", "insufficient_price_data"))
    excluded_no_final_raw = sum(1 for r in all_results if r.skip_reason == "no_final_raw")

    print(f"  排除（價格資料不足）       : {excluded_no_price}")
    if require_final_raw:
        print(f"  排除（缺 final raw data）  : {excluded_no_final_raw}")

    # 按 action_tag 分組
    print_section("各 action_tag 勝率統計")
    by_tag: dict[str, list[LogResult]] = defaultdict(list)
    for r in all_results:
        by_tag[r.log.action_tag or "(none)"].append(r)

    tag_stats = {}
    for tag, tag_results in sorted(by_tag.items()):
        stat = compute_win_rate(tag_results)
        tag_stats[tag] = stat
        included  = stat["total"]
        skipped   = stat["skipped"]
        win_rate  = stat["win_rate"]
        by_reason = stat["skipped_by_reason"]
        print(f"\n  [{tag}]")
        print(f"    納入樣本  : {included}")
        print(f"    排除樣本  : {skipped}  {by_reason if by_reason else ''}")
        print(f"    勝率（5日內下跌>3%）: {win_rate}%")

    # Pearson 相關性（Exit/Trim）
    print_section("Confidence vs 預測結果 Pearson 相關性（Exit / Trim）")
    exit_results = [
        r for r in all_results
        if r.log.action_tag in ("Exit", "Trim")
        and r.skip_reason is None
        and r.log.signal_confidence is not None
    ]
    pearson_stat: dict = {}
    if len(exit_results) < 5:
        print(f"  Exit/Trim 有效樣本不足（{len(exit_results)} < 5），跳過相關性分析。")
    else:
        confidences = [float(r.log.signal_confidence) for r in exit_results]
        outcomes    = [1 if r.pct_change <= -3.0 else 0 for r in exit_results]
        corr, pval  = pearsonr(confidences, outcomes)
        pearson_stat = {"r": round(corr, 3), "p": round(pval, 3), "n": len(exit_results)}
        print(f"  樣本數  : {len(exit_results)}")
        print(f"  r = {corr:.3f}  (p = {pval:.3f})")
        if abs(corr) < 0.2:
            print("  ⚠️  相關性偏低，建議人工審核信心分數閾值設定。")
        else:
            print("  ✅  相關性合理，閾值設定有效。")

    # Confidence 分桶統計
    exit_trim_results = [r for r in all_results if r.log.action_tag in ("Exit", "Trim")]
    if exit_trim_results:
        print_section("Confidence 分桶統計（Exit / Trim）")
        for row in confidence_bucket_stats(exit_trim_results):
            print(f"  [{row['bucket']:>6}]  n={row['n']:>4}  勝率={row['win_rate']}%")

    print("\n⚠️  注意：校準結果需人工審核後才可調整 confidence_scorer.py 的權重。")

    # JSON 輸出
    if output_json:
        payload = {
            "days":                days,
            "require_final_raw":   require_final_raw,
            "data_quality": {
                "total_logs":            total_in_range,
                "final_analysis_count":  len(logs),
                "excluded_not_final":    excluded_not_final,
                "has_final_raw":         has_raw,
                "no_final_raw":          no_raw,
                "excluded_no_price":     excluded_no_price,
                "excluded_no_final_raw": excluded_no_final_raw,
            },
            "tag_stats":    tag_stats,
            "pearson":      pearson_stat,
            "confidence_buckets": confidence_bucket_stats(exit_trim_results) if exit_trim_results else [],
        }
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n結果已寫入 {output_json}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Stock Sentinel 勝率回測")
    parser.add_argument("--mode",                 type=str,   default="position", choices=["position", "new-position"],
                        help="回測模式：position（Exit/Trim，預設）或 new-position（新倉策略）")
    parser.add_argument("--days",                 type=int,   default=90,   help="回測天數（預設 90）")
    parser.add_argument("--hold-days",            type=int,   default=None, help="持有天數，new-position 模式可指定 5/10/20；不指定時預設同時輸出 5/10/20 三組")
    parser.add_argument("--action-tag",           type=str,   default=None, help="篩選特定 action_tag（position 模式）")
    parser.add_argument("--min-confidence",       type=float, default=None, help="最低 signal_confidence 門檻")
    parser.add_argument("--require-final-raw-data", action="store_true",   help="只納入同時有 final raw data 的樣本（position 模式）")
    parser.add_argument("--output-json",          type=str,   default=None, help="輸出結構化 JSON 結果至指定路徑")
    args = parser.parse_args()

    if args.mode == "new-position":
        main_new_position(
            days=args.days,
            hold_days=args.hold_days,
            output_json=args.output_json,
            min_confidence=args.min_confidence,
        )
    else:
        main(
            days=args.days,
            action_tag=args.action_tag,
            require_final_raw=args.require_final_raw_data,
            output_json=args.output_json,
            min_confidence=args.min_confidence,
        )
