# P2 / P3 實作計劃：LLM 評測、策略版本化、Portfolio 整合

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 Roadmap P2 兩項（LLM 輸出評測、策略版本化完整版）與 P3 一項（新倉/持股體驗整合），讓策略系統達到「可驗證、可校準、可觀測、可解釋」目標，並橋接兩條決策路線的 UX。

**Architecture:** Task 1（LLM eval）為純腳本工具；Task 2（策略版本化）觸及 `api.py` 快取邏輯與回測腳本；Task 3（Portfolio 整合）為純前端，不動後端 API。三個 Task 相互獨立，可依序執行。

**Tech Stack:** Python 3.12, pytest, React + TypeScript, react-router-dom v6, Tailwind CSS

**Preconditions:**
- P1 全部完成（策略卡升級、盤中 guardrail、回測持久化）
- P0 前置完成（`strategy_version` 欄位存在）

---

## Task 1：LLM 輸出評測腳本

對應 spec：`docs/specs/p2-llm-output-eval-spec.md`

**Files:**
- Create: `backend/scripts/eval_llm_output.py`
- Create: `backend/tests/fixtures/llm_eval_cases.json`
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`
- Create: `docs/research/llm-eval-results/.gitkeep`

### Step 1：在 langchain_analyzer.py 新增 PROMPT_HASH 計算

找到 `langchain_analyzer.py` 中 `_SYSTEM_PROMPT` 定義後，緊接著加入：

```python
import hashlib as _hashlib
PROMPT_HASH: str = _hashlib.md5(_SYSTEM_PROMPT.encode()).hexdigest()[:8]
```

確認可以 import：

```bash
cd backend && python -c "from ai_stock_sentinel.analysis.langchain_analyzer import PROMPT_HASH; print(PROMPT_HASH)"
```

預期：印出 8 碼 hex 字串（如 `a3f7c891`）

### Step 2：建立 eval 案例集 JSON

建立 `backend/tests/fixtures/llm_eval_cases.json`，含以下 5 筆案例（mock LLM output，不呼叫真實 API）：

```json
[
  {
    "id": "case-001",
    "description": "正常輸出：維度分離、語氣一致、無違規",
    "mock_llm_output": {
      "summary": "台積電技術面維持多頭排列，法人持續買超。",
      "risks": ["若費半指數大幅下跌可能拖累股價"],
      "technical_signal": "bullish",
      "institutional_flow": "institutional_accumulation",
      "sentiment_label": "positive",
      "tech_insight": "均線呈多頭排列，RSI 55，未過熱。",
      "inst_insight": "三大法人連續三日買超，融資小幅增加。",
      "news_insight": "法說會釋出正面展望，市場情緒偏正向。",
      "fundamental_insight": "本益比 18x，低於五年均值。",
      "final_verdict": "三維訊號均偏正向，信心分數中等偏高，策略建議輕倉試探。"
    },
    "conviction_level": "medium",
    "expected_checks": ["json_valid", "no_cross_dimension", "verdict_conviction_align", "no_overconfident_language", "no_fabricated_source"]
  },
  {
    "id": "case-002",
    "description": "違規：tech_insight 引用法人資料",
    "mock_llm_output": {
      "summary": "技術面偏強，法人買超支撐。",
      "risks": [],
      "technical_signal": "bullish",
      "institutional_flow": "institutional_accumulation",
      "sentiment_label": "neutral",
      "tech_insight": "均線多頭，且法人連續買超 5 億，支撐力道強。",
      "inst_insight": "三大法人買超。",
      "news_insight": "無重大消息。",
      "fundamental_insight": null,
      "final_verdict": "整體偏正向。"
    },
    "conviction_level": "medium",
    "expected_checks": ["json_valid", "no_cross_dimension"]
  },
  {
    "id": "case-003",
    "description": "違規：final_verdict 與 low conviction 語氣衝突",
    "mock_llm_output": {
      "summary": "市場不確定性高。",
      "risks": ["外資持續出貨", "技術面轉弱"],
      "technical_signal": "bearish",
      "institutional_flow": "distribution",
      "sentiment_label": "negative",
      "tech_insight": "均線死叉，RSI 降至 40 以下。",
      "inst_insight": "外資連續賣超。",
      "news_insight": "產業新聞偏負面。",
      "fundamental_insight": null,
      "final_verdict": "三維訊號強烈建議積極布局，這是難得的買入機會，強烈推薦。"
    },
    "conviction_level": "low",
    "expected_checks": ["json_valid", "verdict_conviction_align"]
  },
  {
    "id": "case-004",
    "description": "違規：過度武斷措辭",
    "mock_llm_output": {
      "summary": "短線必然上漲。",
      "risks": [],
      "technical_signal": "bullish",
      "institutional_flow": "institutional_accumulation",
      "sentiment_label": "positive",
      "tech_insight": "技術面確定看漲。",
      "inst_insight": "法人一定持續買。",
      "news_insight": "無風險，100% 利多。",
      "fundamental_insight": null,
      "final_verdict": "這支股票一定會漲，確定可以買入。"
    },
    "conviction_level": "high",
    "expected_checks": ["json_valid", "no_overconfident_language"]
  },
  {
    "id": "case-005",
    "description": "違規：引用未出現的來源",
    "mock_llm_output": {
      "summary": "根據高盛報告，目標價上調。",
      "risks": [],
      "technical_signal": "bullish",
      "institutional_flow": "neutral",
      "sentiment_label": "positive",
      "tech_insight": "技術面偏強。",
      "inst_insight": "法人中性。",
      "news_insight": "根據摩根士丹利分析師 John Smith 表示，本季營收超預期。",
      "fundamental_insight": null,
      "final_verdict": "整體偏正向。"
    },
    "conviction_level": "medium",
    "expected_checks": ["json_valid", "no_fabricated_source"]
  }
]
```

### Step 3：建立 eval_llm_output.py 腳本

建立 `backend/scripts/eval_llm_output.py`：

```python
#!/usr/bin/env python3
"""
LLM 輸出品質評測腳本

使用方式：
  python scripts/eval_llm_output.py --dry-run
  python scripts/eval_llm_output.py --output-json docs/research/llm-eval-results/result.json
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
            print("  (dry-run: 跳過 LLM 呼叫)")
            continue

        llm_output = case.get("mock_llm_output", {})
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
```

### Step 4：建立 llm-eval-results 目錄佔位

```bash
mkdir -p docs/research/llm-eval-results
touch docs/research/llm-eval-results/.gitkeep
```

### Step 5：驗證 dry-run 可執行

```bash
cd backend && python scripts/eval_llm_output.py --dry-run
```

預期：印出 5 筆案例標題，無報錯

### Step 6：驗證 mock 輸出評測

```bash
cd backend && python scripts/eval_llm_output.py
```

預期：
- case-001 全 pass
- case-002 `no_cross_dimension` fail
- case-003 `verdict_conviction_align` fail
- case-004 `no_overconfident_language` warn
- case-005 `no_fabricated_source` fail
- 腳本以 exit code 1 結束

### Step 7：輸出第一份評測報告

```bash
cd backend && python scripts/eval_llm_output.py \
  --output-json ../docs/research/llm-eval-results/$(date +%Y%m%d)-baseline.json
```

### Step 8：Commit

```bash
git add backend/scripts/eval_llm_output.py \
        backend/tests/fixtures/llm_eval_cases.json \
        backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py \
        docs/research/llm-eval-results/.gitkeep
git commit -m "feat: LLM 輸出品質評測腳本與 eval 案例集 (P2)"
```

---

## Task 2：策略版本化完整版

對應 spec：`docs/specs/p2-strategy-versioning-spec.md`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Modify: `backend/scripts/backtest_win_rate.py`
- Modify: `docs/development-execution-playbook.md`

### Step 1：確認 AnalyzeResponse 現有欄位

```bash
grep -n "class AnalyzeResponse\|strategy_version\|AnalyzeResponse" backend/src/ai_stock_sentinel/api.py | head -20
```

確認 `AnalyzeResponse` 中是否已有 `strategy_version` 欄位（P0 前置若已加入則跳過）。

### Step 2：寫 AnalyzeResponse strategy_version 的失敗測試

在 `backend/tests/` 找到或建立 API response 的測試，加入：

```python
def test_analyze_response_has_strategy_version(client, mock_graph_result):
    """AnalyzeResponse 應包含 strategy_version 欄位"""
    response = client.post("/analyze", json={"symbol": "2330"})
    assert response.status_code == 200
    data = response.json()
    assert "strategy_version" in data
```

若無 API 整合測試，改為型別檢查測試：

```python
def test_analyze_response_model_has_strategy_version():
    from ai_stock_sentinel.api import AnalyzeResponse
    fields = AnalyzeResponse.model_fields
    assert "strategy_version" in fields
```

### Step 3：在 AnalyzeResponse 加入 strategy_version

找到 `api.py` 中 `AnalyzeResponse` 的 class 定義，加入：

```python
strategy_version: str | None = None
```

在回傳 response 的地方補入：

```python
# 重新分析路徑
strategy_version=STRATEGY_VERSION,

# 快取命中路徑
strategy_version=cache.strategy_version,  # 可能為 NULL（舊資料）
```

### Step 4：在快取讀取加入版本一致性檢查

找到 `_handle_cache_hit()` 函數（api.py 第 137-165 行附近），在函數開頭加入：

```python
# 版本一致性檢查：快取版本與當前版本不一致時，視為失效
if cache.strategy_version != STRATEGY_VERSION:
    logger.info(json.dumps({
        "event": "cache_version_mismatch",
        "symbol": symbol,
        "cache_version": cache.strategy_version,
        "current_version": STRATEGY_VERSION,
    }))
    return None  # 觸發重新分析
```

### Step 5：執行版本一致性檢查測試

```python
def test_cache_version_mismatch_triggers_reanalysis():
    """快取版本與當前版本不一致時，應回傳 None（觸發重分析）"""
    from unittest.mock import MagicMock
    cache = MagicMock()
    cache.strategy_version = "0.9.0"  # 舊版本
    # 模擬 _handle_cache_hit 行為
    # 確認回傳 None
```

（根據實際測試架構調整，可能是直接呼叫函數或用 mock client）

```bash
cd backend && python -m pytest tests/ -k "cache_version" -v
```

### Step 6：在 backtest_win_rate.py 加入 --strategy-version 過濾

找到 `argparse` 設定區（腳本頂部），加入：

```python
parser.add_argument(
    "--strategy-version",
    default=None,
    help="過濾特定策略版本（逗號分隔多個，如 '1.0.0,1.1.0'；'NULL' 表示無版本記錄）",
)
```

在 DB 查詢篩選邏輯中加入過濾：

```python
if args.strategy_version:
    versions = [v.strip() for v in args.strategy_version.split(",")]
    if "NULL" in versions:
        # 包含 NULL 版本
        non_null = [v for v in versions if v != "NULL"]
        if non_null:
            stmt = stmt.where(
                (DailyAnalysisLog.strategy_version.in_(non_null)) |
                (DailyAnalysisLog.strategy_version.is_(None))
            )
        else:
            stmt = stmt.where(DailyAnalysisLog.strategy_version.is_(None))
    else:
        stmt = stmt.where(DailyAnalysisLog.strategy_version.in_(versions))
    print(f"[backtest] 版本過濾：{args.strategy_version}")
```

### Step 7：驗證 --strategy-version 過濾

```bash
cd backend && python scripts/backtest_win_rate.py --mode new-position --days 90 --strategy-version 1.0.0
```

預期：console 印出版本過濾資訊，只計算對應版本的記錄

### Step 8：撰寫版本遞增 SOP

開啟 `docs/development-execution-playbook.md`，在適當章節加入：

```markdown
## 策略版本遞增 SOP

### 觸發條件判斷

| 變更類型 | 版次 | 範例 |
|---|---|---|
| docstring / log / 非邏輯性重構 | PATCH | `1.0.0 → 1.0.1` |
| confidence_scorer.py 常數值、action_plan 文字模板、conviction 降級閾值 | MINOR | `1.0.0 → 1.1.0` |
| generate_strategy() evidence scoring 核心邏輯、strategy_type 分類規則 | MAJOR | `1.0.0 → 2.0.0` |

LLM prompt 修改不屬於策略版本，以 `PROMPT_HASH` 追蹤。

### 操作步驟

1. 確認變更屬於哪個版次（對照上表）
2. 修改 `backend/src/ai_stock_sentinel/config.py` 的 `STRATEGY_VERSION`
3. 執行所有後端測試確認通過
4. 部署後，現有 `StockAnalysisCache` 中的舊版快取自動失效（下次查詢時觸發重分析）
5. 重新執行回測腳本：
   ```bash
   python scripts/backtest_win_rate.py --mode new-position --days 90
   ```
6. 比較新舊版本勝率差異（使用 `--strategy-version` 過濾）

### 注意事項

- 版本遞增後不需要手動清空快取，失效機制自動處理
- 若新版本回測結果不如舊版，可回滾 `STRATEGY_VERSION` 並調查原因
- 每次調整需間隔至少一個月的新樣本，避免 overfitting（信心校準規範）
```

### Step 9：執行完整後端測試確認無回歸

```bash
cd backend && python -m pytest tests/ -v
```

預期：全部通過

### Step 10：Commit

```bash
git add backend/src/ai_stock_sentinel/api.py \
        backend/scripts/backtest_win_rate.py \
        docs/development-execution-playbook.md
git commit -m "feat: 策略版本化完整版（API response 版本欄位、快取版本一致性、回測版本過濾）(P2)"
```

---

## Task 3：Portfolio → Analyze 體驗整合 ❌ 取消

> **決定日期：** 2026-03-19
> **原因：** Spec 的核心 use case（使用者在持股頁想查新倉分析但找不到路）不成立。使用者的持股一定是從 Analyze 頁手動加入的，代表已看過新倉分析，不存在「無路可走」的問題。實作後 revert。

對應 spec：`docs/specs/p3-portfolio-analyze-integration-spec.md`（已加註暫緩）

**Files:**
- Modify: `frontend/src/pages/PortfolioPage.tsx`
- Modify: `frontend/src/pages/AnalyzePage.tsx`

### Step 1：確認 AnalyzePage 是否已用 useSearchParams

```bash
grep -n "useSearchParams\|searchParams\|symbol.*param" frontend/src/pages/AnalyzePage.tsx
```

若無，Step 2 先補上 `useSearchParams` 支援。

### Step 2：在 AnalyzePage 加入 useSearchParams 支援

找到 AnalyzePage 的 import 區與 state 初始化，加入：

```tsx
import { useSearchParams } from "react-router-dom";

// 在元件頂部
const [searchParams] = useSearchParams();

// 在 symbol state 初始化後，加入 effect（只執行一次）
const initialSymbol = searchParams.get("symbol");
useEffect(() => {
  if (initialSymbol) {
    setSymbol(initialSymbol);
    // 自動觸發查詢（假設查詢函數名為 handleAnalyze 或類似）
    handleAnalyze(initialSymbol);
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);  // 刻意只在 mount 時執行一次
```

確認查詢函數的實際名稱（可能是 `handleSubmit`、`handleAnalyze` 等），以實際名稱替換。

### Step 3：擴充 AnalyzePage 的 portfolio state 為含 entry_price

目前 `AnalyzePage` 有 `portfolioSymbols: Set<string>` state，擴充為含完整 item 的陣列：

```tsx
interface PortfolioItem {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  entry_date: string;
}

// 把原本的 portfolioSymbols state 改為
const [portfolioItems, setPortfolioItems] = useState<PortfolioItem[]>([]);

// 原本的 fetch portfolio 邏輯改為存完整 items（而非只存 symbol set）
// GET /portfolio 回傳格式確認：陣列，含 symbol、entry_price
```

更新所有使用 `portfolioSymbols` 的地方，改為 `portfolioItems.some(i => i.symbol === symbol)`。

### Step 4：寫「已持有」橫幅元件

在 `AnalyzePage.tsx` 加入 `HeldPositionBanner` 元件：

```tsx
function HeldPositionBanner({
  item,
  currentPrice,
}: {
  item: PortfolioItem;
  currentPrice: number | undefined;
}) {
  const pct = currentPrice != null
    ? ((currentPrice - item.entry_price) / item.entry_price * 100).toFixed(1)
    : null;

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950 px-4 py-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-amber-700 dark:text-amber-400">已持有</span>
        <span className="text-sm text-text-secondary">
          成本 {item.entry_price.toFixed(2)}
          {pct != null && (
            <span className={`ml-1 font-medium ${parseFloat(pct) >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
              {parseFloat(pct) >= 0 ? "+" : ""}{pct}%
            </span>
          )}
        </span>
      </div>
      <a
        href="/portfolio"
        className="text-xs text-text-muted underline hover:text-text-secondary"
      >
        查看持股診斷
      </a>
    </div>
  );
}
```

在策略卡上方（分析結果區開頭）插入：

```tsx
{(() => {
  const held = portfolioItems.find(i => i.symbol === symbol);
  if (!held || !result) return null;
  return (
    <HeldPositionBanner
      item={held}
      currentPrice={result.snapshot?.current_price}
    />
  );
})()}
```

### Step 5：在 PortfolioPage 每筆持股卡片加入「查看新倉分析」按鈕

找到 `PortfolioPage.tsx` 的持股卡片渲染區（有 `item.symbol` 的地方），確認 props 有 `onNavigateAnalyze`。

```tsx
// 在持股卡片的操作按鈕區（與「執行持倉診斷」同層）加入：
<button
  onClick={() => {
    // 使用 onNavigateAnalyze callback（props 已定義）
    // 或直接用 useNavigate（需確認 PortfolioPage 是否已用 react-router）
    window.location.href = `/analyze?symbol=${item.symbol}`;
  }}
  className="text-xs text-text-muted border border-border rounded-lg px-2 py-1 hover:bg-card-hover transition"
>
  新倉分析
</button>
```

若 `PortfolioPage` 已有 `useNavigate`（需檢查），改為：

```tsx
const navigate = useNavigate();
// ...
onClick={() => navigate(`/analyze?symbol=${item.symbol}`)}
```

若沒有 `useNavigate`，先在 import 加入：

```tsx
import { useNavigate } from "react-router-dom";
```

### Step 6：目視驗收

```bash
cd frontend && npm run dev
```

確認：
1. Portfolio 頁每筆持股有「新倉分析」按鈕
2. 點擊後跳至 Analyze，搜尋框有代號，自動查詢
3. 若該股在 portfolio 中，顯示「已持有」橫幅
4. 橫幅的「查看持股診斷」連結跳回 `/portfolio`
5. 直接在 Analyze 手動輸入代號，功能不受影響

### Step 7：確認無 JS console error

開啟瀏覽器 devtools，確認無未處理的 exception（尤其是 `portfolioItems` fetch 失敗時的靜默處理）。

### Step 8：Commit

```bash
git add frontend/src/pages/AnalyzePage.tsx frontend/src/pages/PortfolioPage.tsx
git commit -m "feat: Portfolio→Analyze 導航整合，Analyze 顯示已持有提示 (P3)"
```

---

## Spec Review

### 對照 `docs/specs/p2-llm-output-eval-spec.md`

| Spec 項目 | 對應 Task | 確認點 |
|---|---|---|
| F1-1：案例集路徑 | Task 1 Step 2 | `backend/tests/fixtures/llm_eval_cases.json` |
| F1-3：5 類案例 | Task 1 Step 2 | case-001~005 覆蓋正常、維度越界、衝突、武斷、造假 |
| F2-1：`--cases` 參數 | Task 1 Step 3 | `argparse --cases` |
| F2-3：`--dry-run` | Task 1 Step 5 | 跳過 LLM 呼叫 |
| F2-4：三態結果 | Task 1 Step 3 | `pass/warn/fail` |
| F3（check 種類） | Task 1 Step 3 | 5 種 check 函數全實作 |
| F4-1：PROMPT_HASH | Task 1 Step 1 | MD5 前 8 碼 |
| F5-4：fail 時 exit 1 | Task 1 Step 3 | `sys.exit(1)` |
| AC3：手動注入違規輸出 | Task 1 Step 6 | case-002 `no_cross_dimension` fail |

### 對照 `docs/specs/p2-strategy-versioning-spec.md`

| Spec 項目 | 對應 Task | 確認點 |
|---|---|---|
| F1-1：AnalyzeResponse 加入 strategy_version | Task 2 Step 2-3 | nullable str 欄位 |
| F1-3：快取命中時回傳快取的版本 | Task 2 Step 3 | `cache.strategy_version` |
| F1-4：NULL 時回傳 null | Task 2 Step 3 | 不填入當前版本 |
| F2-1：版本不一致 → cache miss | Task 2 Step 4 | `return None` |
| F2-3：NULL 視為版本失效 | Task 2 Step 4 | `!= STRATEGY_VERSION` 含 None 比對 |
| F2-4：失效記錄不刪除 | Task 2 Step 4 | 只 return None，不執行 DELETE |
| F3-1：`--strategy-version` 參數 | Task 2 Step 6 | argparse 加入 |
| F3-4：NULL 過濾 | Task 2 Step 6 | `strategy_version IS NULL` |
| F4-1：playbook SOP | Task 2 Step 8 | 觸發條件表 + 操作步驟 |
| AC2：升版後舊快取失效 | Task 2 Step 5 | 測試版本不一致時 return None |

### 對照 `docs/specs/p3-portfolio-analyze-integration-spec.md`

| Spec 項目 | 對應 Task | 確認點 |
|---|---|---|
| F1-1：持股卡片有「查看新倉分析」按鈕 | Task 3 Step 5 | 次要樣式按鈕 |
| F1-2：點擊跳至 /analyze 並自動查詢 | Task 3 Step 5 | navigate + Step 2 的 useSearchParams effect |
| F1-3：使用 react-router navigate | Task 3 Step 5 | `useNavigate` 或 `window.location.href` |
| F1-4：AnalyzePage 讀取 searchParams | Task 3 Step 2 | `useSearchParams().get("symbol")` |
| F2-1：fetch portfolio 清單 | Task 3 Step 3 | 擴充現有 portfolio fetch |
| F2-2：顯示「已持有」橫幅 | Task 3 Step 4 | `HeldPositionBanner` 元件 |
| F2-3：橫幅顯示成本價、損益 | Task 3 Step 4 | `entry_price`、`pct` 計算 |
| F2-4：橫幅含「查看持股診斷」連結 | Task 3 Step 4 | `<a href="/portfolio">` |
| F2-5：fetch 失敗靜默略過 | Task 3 Step 4/7 | try-catch 不影響分析功能 |
| NF2：手動輸入代號不受影響 | Task 3 Step 6 | AC6 目視確認 |
| AC7：Portfolio API 失敗不報錯 | Task 3 Step 7 | devtools 無 exception |
