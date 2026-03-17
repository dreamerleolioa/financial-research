#!/usr/bin/env python
# backend/scripts/backtest_win_rate.py
"""
勝率回測腳本

用法：
    python scripts/backtest_win_rate.py --days 90
    python scripts/backtest_win_rate.py --days 90 --action-tag Exit
    python scripts/backtest_win_rate.py --days 90 --require-final-raw-data
    python scripts/backtest_win_rate.py --days 90 --output-json result.json

定義：
    勝率 = Exit/Trim 訊號發出後 5 個交易日內，股價下跌 > 3% 的比率

回測口徑：
    - 主口徑為「分析訊號準確率」，非純價格統計
    - 預設只納入 analysis_is_final = TRUE 的樣本
    - 可選 --require-final-raw-data 進一步限制只納入同時有 final raw data 的樣本
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


def print_section(title: str) -> None:
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    parser.add_argument("--days",                 type=int,   default=90,   help="回測天數（預設 90）")
    parser.add_argument("--action-tag",           type=str,   default=None, help="篩選特定 action_tag")
    parser.add_argument("--min-confidence",       type=float, default=None, help="最低 signal_confidence 門檻")
    parser.add_argument("--require-final-raw-data", action="store_true",   help="只納入同時有 final raw data 的樣本")
    parser.add_argument("--output-json",          type=str,   default=None, help="輸出結構化 JSON 結果至指定路徑")
    args = parser.parse_args()
    main(
        days=args.days,
        action_tag=args.action_tag,
        require_final_raw=args.require_final_raw_data,
        output_json=args.output_json,
        min_confidence=args.min_confidence,
    )
