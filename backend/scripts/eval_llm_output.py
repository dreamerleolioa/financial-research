#!/usr/bin/env python3
"""
LLM 輸出品質評測腳本

使用方式：
  python scripts/eval_llm_output.py --dry-run
  python scripts/eval_llm_output.py --output-json eval-results/result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# ─── 檢查規則 ─────────────────────────────────────────────────────────────────

CROSS_DIM_RULES = {
    "tech_insight": ["法人", "買超", "賣超", "外資", "投信", "自營"],
    "inst_insight": ["RSI", "均線", "MA", "支撐", "壓力", "KD"],
    "news_insight": ["RSI", "均線", "MA"],
}

OVERCONFIDENT_WORDS = ["必然", "確定", "100%", "一定會", "一定", "必定", "絕對"]
FABRICATED_SOURCE_PATTERNS = [
    "根據.*分析師", ".*表示，", "根據高盛", "根據摩根", "根據瑞銀", "根據花旗"
]

LOW_CONVICTION_ASSERTIVE = ["強烈建議", "積極布局", "強烈推薦", "難得的買入機會"]
HIGH_CONVICTION_PESSIMISTIC = ["謹慎觀望", "不建議", "暫不進場", "風險極高"]


def check_json_valid(output: dict) -> tuple[str, str]:
    required_keys = ["final_verdict", "tech_insight", "inst_insight"]
    missing = [k for k in required_keys if k not in output]
    if missing:
        return "fail", f"缺少必要欄位：{missing}"
    return "pass", "JSON 結構完整"


def check_no_cross_dimension(output: dict) -> tuple[str, str]:
    violations = []
    for field, forbidden_words in CROSS_DIM_RULES.items():
        text = output.get(field, "") or ""
        for word in forbidden_words:
            if word in text:
                violations.append(f"{field} 含禁用字：「{word}」")
    if violations:
        return "fail", "; ".join(violations)
    return "pass", "維度隔離正常"


def check_verdict_conviction_align(output: dict, conviction_level: str) -> tuple[str, str]:
    verdict = output.get("final_verdict", "") or ""
    if conviction_level == "low":
        for phrase in LOW_CONVICTION_ASSERTIVE:
            if phrase in verdict:
                return "fail", f"low conviction 但 final_verdict 含積極語氣：「{phrase}」"
    elif conviction_level == "high":
        for phrase in HIGH_CONVICTION_PESSIMISTIC:
            if phrase in verdict:
                return "fail", f"high conviction 但 final_verdict 含悲觀語氣：「{phrase}」"
    return "pass", "語氣與信心等級一致"


def check_no_overconfident_language(output: dict) -> tuple[str, str]:
    all_text = " ".join(str(v) for v in output.values() if v)
    hits = [w for w in OVERCONFIDENT_WORDS if w in all_text]
    if hits:
        return "warn", f"含過度武斷措辭：{hits}"
    return "pass", "無過度武斷措辭"


def check_no_fabricated_source(output: dict) -> tuple[str, str]:
    import re
    all_text = " ".join(str(v) for v in output.values() if v)
    for pattern in FABRICATED_SOURCE_PATTERNS:
        if re.search(pattern, all_text):
            return "fail", f"含疑似造假來源：pattern=「{pattern}」"
    return "pass", "無造假來源跡象"


CHECK_FN_MAP = {
    "json_valid": lambda output, _: check_json_valid(output),
    "no_cross_dimension": lambda output, _: check_no_cross_dimension(output),
    "verdict_conviction_align": lambda output, case: check_verdict_conviction_align(
        output, case.get("conviction_level", "medium")
    ),
    "no_overconfident_language": lambda output, _: check_no_overconfident_language(output),
    "no_fabricated_source": lambda output, _: check_no_fabricated_source(output),
}


# ─── 執行評測 ─────────────────────────────────────────────────────────────────

def _call_llm(mock_input: str) -> dict:
    """用 mock_input 直接呼叫 LLM，回傳解析後的 dict。"""
    import os
    from ai_stock_sentinel.analysis.langchain_analyzer import _SYSTEM_PROMPT

    try:
        import anthropic
    except ImportError:
        raise RuntimeError("需要安裝 anthropic 套件：pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 ANTHROPIC_API_KEY 環境變數")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": mock_input}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        raw = "\n".join(inner).strip()
    return json.loads(raw)


def run_checks(case: dict, llm_output: dict) -> list[dict]:
    results = []
    for check_name in case.get("expected_checks", []):
        fn = CHECK_FN_MAP.get(check_name)
        if fn is None:
            results.append({"check": check_name, "result": "warn", "message": f"未知的 check：{check_name}"})
            continue
        status, message = fn(llm_output, case)
        results.append({"check": check_name, "result": status, "message": message})
    return results


def run_eval(cases: list[dict], dry_run: bool) -> dict:
    from ai_stock_sentinel.analysis.langchain_analyzer import PROMPT_HASH

    all_case_results = []
    pass_count = warn_count = fail_count = 0

    for case in cases:
        print(f"\n[{case['id']}] {case['description']}")

        if dry_run:
            print("  (dry-run: 跳過 LLM 呼叫，以 mock_llm_output 執行 checks)")
            llm_output = case.get("mock_llm_output", {})
        else:
            mock_input = case.get("mock_input")
            if not mock_input:
                print("  [warn] 缺少 mock_input，跳過此案例")
                continue
            try:
                llm_output = _call_llm(mock_input)
                print("  LLM 回應已取得")
            except Exception as e:
                print(f"  [error] LLM 呼叫失敗：{e}", file=sys.stderr)
                all_case_results.append({
                    "id": case["id"],
                    "description": case["description"],
                    "checks": [{"check": "llm_call", "result": "fail", "message": str(e)}],
                })
                fail_count += 1
                continue

        checks = run_checks(case, llm_output)

        for c in checks:
            icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(c["result"], "?")
            print(f"  {icon} [{c['result'].upper()}] {c['check']}: {c['message']}")
            if c["result"] == "pass":
                pass_count += 1
            elif c["result"] == "warn":
                warn_count += 1
            elif c["result"] == "fail":
                fail_count += 1

        all_case_results.append({
            "id": case["id"],
            "description": case["description"],
            "checks": checks,
        })

    return {
        "run_date": str(date.today()),
        "prompt_hash": PROMPT_HASH,
        "total": sum(len(c.get("expected_checks", [])) for c in cases),
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "cases": all_case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 輸出品質評測")
    parser.add_argument("--cases", default="tests/fixtures/llm_eval_cases.json")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"[error] 找不到案例集：{cases_path}", file=sys.stderr)
        sys.exit(1)

    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    print(f"載入 {len(cases)} 筆 eval 案例（prompt: {args.cases}）")

    report = run_eval(cases, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"\n=== 評測結果 ===")
        print(f"Pass: {report['pass_count']} / Warn: {report['warn_count']} / Fail: {report['fail_count']}")

        if args.output_json:
            out_path = Path(args.output_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"結果已寫入：{out_path}")

        if report["fail_count"] > 0:
            print(f"\n[!] 有 {report['fail_count']} 個 fail，請確認 LLM 輸出品質。", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
