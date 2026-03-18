#!/usr/bin/env python
# backend/scripts/analyze_confidence_breakdown.py
"""
信心分數維度貢獻分析腳本（一次性校準工具）

用途：
    分析各訊號維度（inst_flow / sentiment / technical）對新倉策略勝率的獨立貢獻，
    協助判斷 confidence_scorer.py 的權重是否需要校準。

用法：
    python scripts/analyze_confidence_breakdown.py --days 90
    python scripts/analyze_confidence_breakdown.py --days 90 --output-json breakdown.json

輸出：
    三組維度分析：
    1. inst_flow 分組：institutional_accumulation / distribution / retail_chasing / neutral
    2. sentiment_label 分組：positive / negative / neutral
    3. technical_signal 分組：bullish / bearish / sideways

注意：
    - 此腳本只讀取 DB，不寫入任何資料
    - indicators.flow_label / sentiment_label / technical_signal 欄位需在 2026-03 後的資料才有值
    - 舊資料缺少這些欄位時，該筆記錄計入 skip 數

設計決策：
    此為一次性校準腳本，不應長期維護。
    若需重複分析，請確認各分桶樣本數 >= 10，否則結論不可靠。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta

import yfinance as yf

from ai_stock_sentinel.db.models import DailyAnalysisLog
from ai_stock_sentinel.db.session import _get_session_local


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIN_THRESHOLD_PCT = 3.0
HOLD_DAYS = 5


# ---------------------------------------------------------------------------
# Price helpers (简化版，不含 full cache)
# ---------------------------------------------------------------------------

_price_cache: dict[str, dict] = {}


def _ensure_prices(symbol: str, start: date, end: date) -> None:
    if symbol not in _price_cache:
        _price_cache[symbol] = {}
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=start.isoformat(), end=end.isoformat())
    for ts, row in hist.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        _price_cache[symbol][d] = float(row["Close"])


def _get_nth_trading_close(symbol: str, from_date: date, n: int) -> float | None:
    cache = _price_cache.get(symbol, {})
    sorted_dates = sorted(d for d in cache if d >= from_date)
    if len(sorted_dates) <= n:
        return None
    return cache[sorted_dates[n]]


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_new_position_logs(days: int) -> list[DailyAnalysisLog]:
    """讀取新倉策略的 final 分析 log。"""
    since = date.today() - timedelta(days=days)
    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        logs = (
            db.query(DailyAnalysisLog)
            .filter(
                DailyAnalysisLog.record_date >= since,
                DailyAnalysisLog.analysis_is_final.is_(True),
                DailyAnalysisLog.indicators["strategy_type"].astext.in_(["short_term", "mid_term"]),
            )
            .all()
        )
    return logs


def prefetch_prices(logs: list[DailyAnalysisLog]) -> None:
    """批次預抓價格。"""
    by_symbol: dict[str, list[date]] = defaultdict(list)
    for log in logs:
        by_symbol[log.symbol].append(log.record_date)
    for symbol, dates in by_symbol.items():
        start = min(dates)
        end = max(dates) + timedelta(days=15)
        try:
            _ensure_prices(symbol, start, end)
        except Exception as exc:
            print(f"  ⚠️  {symbol} 價格抓取失敗：{exc}")


# ---------------------------------------------------------------------------
# Win-rate computation
# ---------------------------------------------------------------------------

def compute_group_stats(logs: list[DailyAnalysisLog]) -> dict:
    """計算一組 log 的新倉 5日勝率統計。"""
    wins = losses = neutral_count = no_price = 0
    for log in logs:
        p0 = _get_nth_trading_close(log.symbol, log.record_date, 0)
        pn = _get_nth_trading_close(log.symbol, log.record_date, HOLD_DAYS)
        if p0 is None or pn is None:
            no_price += 1
            continue
        pct = (pn - p0) / p0 * 100
        if pct > WIN_THRESHOLD_PCT:
            wins += 1
        elif pct < -WIN_THRESHOLD_PCT:
            losses += 1
        else:
            neutral_count += 1

    decisive = wins + losses
    return {
        "n": len(logs),
        "no_price": no_price,
        "wins": wins,
        "losses": losses,
        "neutral": neutral_count,
        "decisive": decisive,
        "win_rate": round(wins / decisive * 100, 1) if decisive else None,
    }


# ---------------------------------------------------------------------------
# Dimension extractors
# ---------------------------------------------------------------------------

def get_flow_label(log: DailyAnalysisLog) -> str | None:
    return (log.indicators or {}).get("flow_label")


def get_sentiment_label(log: DailyAnalysisLog) -> str | None:
    return (log.indicators or {}).get("sentiment_label")


def get_technical_signal(log: DailyAnalysisLog) -> str | None:
    return (log.indicators or {}).get("technical_signal")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_dimension(
    logs: list[DailyAnalysisLog],
    label_fn,
    dimension_name: str,
) -> dict:
    """按維度標籤分組計算勝率。"""
    by_label: dict[str, list[DailyAnalysisLog]] = defaultdict(list)
    skipped = 0
    for log in logs:
        label = label_fn(log)
        if label is None:
            skipped += 1
        else:
            by_label[label].append(log)

    rows = []
    for label, group_logs in sorted(by_label.items()):
        stat = compute_group_stats(group_logs)
        rows.append({"label": label, **stat})

    return {
        "dimension": dimension_name,
        "skipped_missing_field": skipped,
        "groups": rows,
    }


def print_dimension_result(result: dict) -> None:
    dim = result["dimension"]
    skipped = result["skipped_missing_field"]
    print(f"\n[{dim}]")
    if skipped:
        print(f"  ⚠️  {skipped} 筆缺少此維度欄位（可能是舊資料），已跳過")
    header = f"  {'標籤':>24}  {'n':>4}  {'5日勝率':>8}  {'勝':>4}  {'敗':>4}  {'平':>4}"
    print(header)
    for row in result["groups"]:
        wr = row["win_rate"]
        wr_str = f"{wr}%" if wr is not None else "n/a"
        n_flag = " (n<5)" if row["n"] < 5 else ""
        print(
            f"  {row['label']:>24}  {row['n']:>4}  {wr_str:>8}  "
            f"{row['wins']:>4}  {row['losses']:>4}  {row['neutral']:>4}{n_flag}"
        )

    # 簡單診斷：inst_flow / sentiment 的預期方向
    if result["dimension"] == "inst_flow":
        acc_wr = next((r["win_rate"] for r in result["groups"] if r["label"] == "institutional_accumulation"), None)
        dist_wr = next((r["win_rate"] for r in result["groups"] if r["label"] == "distribution"), None)
        if acc_wr is not None and dist_wr is not None:
            if acc_wr > dist_wr:
                print("  ✅  inst_flow 方向正確（accumulation 勝率 > distribution）")
            else:
                print("  ⚠️  inst_flow 方向異常（accumulation 勝率 <= distribution），建議檢視 inst_flow 權重設計")

    if result["dimension"] == "sentiment_label":
        pos_wr = next((r["win_rate"] for r in result["groups"] if r["label"] == "positive"), None)
        neg_wr = next((r["win_rate"] for r in result["groups"] if r["label"] == "negative"), None)
        if pos_wr is not None and neg_wr is not None:
            if pos_wr > neg_wr:
                print("  ✅  sentiment 方向正確（positive 勝率 > negative）")
            else:
                print("  ⚠️  sentiment 方向異常（positive 勝率 <= negative），建議檢視 sentiment 權重設計")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(days: int, output_json: str | None) -> None:
    print(f"\n=== 信心分數維度貢獻分析（過去 {days} 天，持有 {HOLD_DAYS} 日，門檻 ±{WIN_THRESHOLD_PCT}%）===")

    logs = fetch_new_position_logs(days)
    print(f"  新倉策略 final log 數：{len(logs)}")

    if not logs:
        print("\n無符合條件的記錄。")
        return

    prefetch_prices(logs)

    dimensions = [
        analyze_dimension(logs, get_flow_label,       "inst_flow"),
        analyze_dimension(logs, get_sentiment_label,  "sentiment_label"),
        analyze_dimension(logs, get_technical_signal, "technical_signal"),
    ]

    print("\n" + "=" * 60)
    print("  維度貢獻分析結果")
    print("=" * 60)
    for result in dimensions:
        print_dimension_result(result)

    print("\n\n⚠️  注意：")
    print("  - 各分組 n < 5 時結論不可靠，n >= 10 才可初步參考")
    print("  - 此分析為一次性校準工具，結論需人工審核後才可修改 confidence_scorer.py")
    print("  - 如需產出調權提案，存入 docs/research/confidence-calibration-proposals/")

    if output_json:
        payload = {
            "mode":              "confidence-breakdown",
            "days":              days,
            "hold_days":         HOLD_DAYS,
            "win_threshold_pct": WIN_THRESHOLD_PCT,
            "total_logs":        len(logs),
            "dimensions":        dimensions,
        }
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n結果已寫入 {output_json}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="信心分數維度貢獻分析腳本")
    parser.add_argument("--days",        type=int, default=90,  help="回測天數（預設 90）")
    parser.add_argument("--output-json", type=str, default=None, help="輸出 JSON 至指定路徑")
    args = parser.parse_args()

    main(days=args.days, output_json=args.output_json)
