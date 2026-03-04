#!/usr/bin/env python3
"""
驗證腳本：確認 InstitutionalFlowRouter 可正常拉取法人資料。

用法：
    cd backend
    python utils/verify_institutional_flow.py
    python utils/verify_institutional_flow.py --symbol 6488.TWO
    python utils/verify_institutional_flow.py --symbol 2330.TW --days 3

環境變數：
    FINMIND_API_TOKEN：FinMind API Token（選填，免費方案有限流）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# 確保可從 backend 根目錄執行
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ai_stock_sentinel.data_sources.institutional_flow import (
    FinMindProvider,
    InstitutionalFlowRouter,
    TpexProvider,
    TwseOpenApiProvider,
)
from ai_stock_sentinel.data_sources.institutional_flow.tools import fetch_institutional_flow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_institutional_flow")


def _build_router() -> InstitutionalFlowRouter:
    token = os.environ.get("FINMIND_API_TOKEN", "")
    return InstitutionalFlowRouter(
        providers=[
            FinMindProvider(api_token=token),
            TwseOpenApiProvider(),
            TpexProvider(),
        ]
    )


def verify_symbol(symbol: str, days: int, router: InstitutionalFlowRouter) -> bool:
    """驗證單一 symbol，印出結果，回傳是否成功。"""
    print(f"\n{'='*60}")
    print(f"  驗證：{symbol}（days={days}）")
    print(f"{'='*60}")

    result = fetch_institutional_flow(symbol=symbol, days=days, router=router)

    if "error" in result:
        print(f"[FAIL] 錯誤碼：{result['error']}")
        print(f"       訊息：{result.get('error_message', '')}")
        return False

    # 輸出關鍵欄位
    required_fields = ["foreign_buy", "investment_trust_buy", "dealer_buy", "margin_delta"]
    print(f"[OK] source_provider = {result.get('source_provider', 'N/A')}")
    print(f"     flow_label       = {result.get('flow_label', 'N/A')}")
    print()

    all_present = True
    for field in required_fields:
        val = result.get(field)
        status = "OK" if val is not None else "WARN(None)"
        print(f"     {field:<30} = {val!r:>15}  [{status}]")
        if val is None:
            all_present = False

    if result.get("warnings"):
        print()
        print("  [告警]")
        for w in result["warnings"]:
            print(f"    - {w}")

    print()
    print("  完整 JSON：")
    print(json.dumps(result, ensure_ascii=False, indent=4, default=str))

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="驗證法人籌碼資料源")
    parser.add_argument("--symbol", default=None, help="指定單一股票代碼（預設跑 2330.TW + 6488.TWO）")
    parser.add_argument("--days", type=int, default=5, help="回溯天數（預設 5）")
    args = parser.parse_args()

    router = _build_router()

    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = ["2330.TW", "6488.TWO"]

    results: dict[str, bool] = {}
    for sym in symbols:
        results[sym] = verify_symbol(symbol=sym, days=args.days, router=router)

    # 摘要
    print(f"\n{'='*60}")
    print("  驗收摘要")
    print(f"{'='*60}")
    all_pass = True
    for sym, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {sym:<20} {status}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print("  [全部通過] 資料源可正常運作")
        sys.exit(0)
    else:
        print("  [部分失敗] 請檢查網路連線或 API Token")
        sys.exit(1)


if __name__ == "__main__":
    main()
