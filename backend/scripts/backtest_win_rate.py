#!/usr/bin/env python
# backend/scripts/backtest_win_rate.py
"""
勝率回測腳本（Phase 8）

用法：
    python scripts/backtest_win_rate.py --days 90
    python scripts/backtest_win_rate.py --days 90 --action-tag Exit

定義：
    勝率 = Exit/Trim 訊號發出後 5 個交易日內，股價下跌 > 3% 的比率

輸出：
    各 action_tag 的勝率統計 + 各維度分數與預測結果的 Pearson 相關性
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta

import yfinance as yf
from scipy.stats import pearsonr
from sqlalchemy.orm import Session

from ai_stock_sentinel.db.models import DailyAnalysisLog
from ai_stock_sentinel.db.session import _get_session_local


def fetch_logs(days: int, action_tag: str | None, db: Session) -> list[DailyAnalysisLog]:
    """從 DB 讀取指定期間的診斷 log。"""
    since = date.today() - timedelta(days=days)
    q = db.query(DailyAnalysisLog).filter(DailyAnalysisLog.record_date >= since)
    if action_tag:
        q = q.filter(DailyAnalysisLog.action_tag == action_tag)
    return q.all()


def fetch_price_5d_later(symbol: str, signal_date: date) -> float | None:
    """用 yfinance 取得訊號日後第 5 個交易日的收盤價。"""
    end = signal_date + timedelta(days=10)  # 多取幾天確保涵蓋 5 個交易日
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=signal_date.isoformat(), end=end.isoformat())
    if len(hist) < 5:
        return None
    return float(hist["Close"].iloc[4])


def fetch_signal_price(symbol: str, signal_date: date) -> float | None:
    """取得訊號日的收盤價。"""
    end = signal_date + timedelta(days=2)
    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=signal_date.isoformat(), end=end.isoformat())
    if hist.empty:
        return None
    return float(hist["Close"].iloc[0])


def compute_win_rate(logs: list, threshold_pct: float = -3.0) -> dict:
    """計算 Exit/Trim 訊號的勝率（訊號後 5 日內下跌 > threshold_pct）。"""
    total = 0
    correct = 0
    skipped = 0

    for log in logs:
        p0 = fetch_signal_price(log.symbol, log.record_date)
        p5 = fetch_price_5d_later(log.symbol, log.record_date)

        if p0 is None or p5 is None:
            skipped += 1
            continue

        pct_change = (p5 - p0) / p0 * 100
        total += 1
        if pct_change <= threshold_pct:
            correct += 1

    return {
        "total":    total,
        "correct":  correct,
        "skipped":  skipped,
        "win_rate": round(correct / total * 100, 1) if total > 0 else None,
    }


def main(days: int, action_tag: str | None) -> None:
    print(f"\n=== 勝率回測報告（過去 {days} 天）===\n")

    SessionLocal = _get_session_local()
    with SessionLocal() as db:
        logs = fetch_logs(days, action_tag, db)

    if not logs:
        print("無符合條件的診斷紀錄。")
        return

    # 按 action_tag 分組統計勝率
    by_tag: dict[str, list] = defaultdict(list)
    for log in logs:
        by_tag[log.action_tag].append(log)

    for tag, tag_logs in sorted(by_tag.items()):
        result = compute_win_rate(tag_logs)
        print(f"[{tag}]")
        print(f"  訊號次數：{result['total']}（跳過：{result['skipped']}）")
        print(f"  勝率（5日內下跌>3%）：{result['win_rate']}%\n")

    # Pearson 相關性分析（僅針對有 signal_confidence 的 Exit/Trim 訊號）
    exit_logs = [l for l in logs if l.action_tag in ("Exit", "Trim") and l.signal_confidence]
    if len(exit_logs) < 5:
        print("Exit/Trim 訊號筆數不足（< 5），跳過相關性分析。")
        return

    confidences = []
    outcomes = []
    for log in exit_logs:
        p0 = fetch_signal_price(log.symbol, log.record_date)
        p5 = fetch_price_5d_later(log.symbol, log.record_date)
        if p0 and p5:
            confidences.append(float(log.signal_confidence))
            outcomes.append(1 if (p5 - p0) / p0 * 100 <= -3.0 else 0)

    if len(confidences) >= 5:
        corr, pval = pearsonr(confidences, outcomes)
        print("=== 信心分數 vs 預測結果 Pearson 相關性 ===")
        print(f"  r = {corr:.3f}  (p = {pval:.3f})")
        if abs(corr) < 0.2:
            print("  ⚠️  相關性偏低，建議人工審核信心分數閾值設定。")
        else:
            print("  ✅  相關性合理，閾值設定有效。")

    print("\n注意：校準結果需人工審核後才可調整 confidence_scorer.py 的權重。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Stock Sentinel 勝率回測")
    parser.add_argument("--days",       type=int, default=90,   help="回測天數（預設 90）")
    parser.add_argument("--action-tag", type=str, default=None, help="篩選特定 action_tag")
    args = parser.parse_args()
    main(args.days, args.action_tag)
