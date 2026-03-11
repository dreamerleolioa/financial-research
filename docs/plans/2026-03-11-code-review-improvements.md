# Code Review Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 依據 PR code review 的四大類建議，逐步改善後端程式碼的可維護性、型別安全、非同步效能與日誌品質。

**Architecture:** 本計劃分為五個獨立任務，每個任務對應一類改進建議。任務間彼此低耦合，可獨立執行，但建議依序完成以確保測試覆蓋正確性。改進以最小差異為原則，不重寫業務邏輯。

**Tech Stack:** Python 3.10+, LangChain Core (`langchain_core`), `httpx`, `logging` standard library, Pydantic v2, pytest

---

## 背景：PR 建議摘要

| 類別 | 問題 | 目標 |
|------|------|------|
| 1. 狀態管理 | `GraphState` 超過 40 個平鋪欄位 | 依功能分組為子 TypedDict |
| 2. LLM 解析 | 手動 strip markdown + `json.loads` | 改用 `JsonOutputParser` |
| 3. 循環引用 | `confidence_scorer.py` 內有 local import | 將共用計算函式提取至 `analysis/metrics.py` |
| 4. RSS 容錯 | 解析錯誤靜默回傳空列表，無日誌 | 加入錯誤日誌與 HTTP 錯誤碼記錄 |
| 5. Logging | 大量 `print` / `json.dumps` | 引入 `logging` 模組統一輸出 |

---

## Task 1: 提取共用計算函式至 `analysis/metrics.py`（解決循環引用）

**背景：** `confidence_scorer.py:22` 使用 `from ai_stock_sentinel.analysis.context_generator import ma as calc_ma` 的 local import，原因是 `context_generator` 與 `confidence_scorer` 之間存在循環引用風險。解法是將純計算函式（`ma`、`calc_rsi`、`calc_bias`）提取到獨立的 `metrics.py`，讓兩個模組都從這裡 import。

**Files:**
- Create: `backend/src/ai_stock_sentinel/analysis/metrics.py`
- Modify: `backend/src/ai_stock_sentinel/analysis/context_generator.py`
- Modify: `backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`
- Test: `backend/tests/analysis/test_metrics.py`（如果已存在 `test_context_generator.py` 需確認不破壞現有測試）

**Step 1: 確認現有測試通過（baseline）**

```bash
cd backend
python -m pytest tests/ -x -q 2>&1 | tail -20
```
Expected: 所有測試 PASS（記錄當前通過數量）

**Step 2: 建立 `metrics.py`，把三個純計算函式搬過去**

```python
# backend/src/ai_stock_sentinel/analysis/metrics.py
"""純數學計算工具，不依賴任何專案內模組（無循環引用風險）。"""
from __future__ import annotations


def ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def calc_bias(close: float, ma_val: float) -> float | None:
    """BIAS = (close - MA) / MA * 100"""
    if ma_val == 0:
        return None
    return (close - ma_val) / ma_val * 100


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI 標準公式（Wilder 平均法）。資料不足時回傳 None。"""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
```

**Step 3: 更新 `context_generator.py`，改從 metrics import**

在 `context_generator.py` 的 import 區段（約第 10 行），新增：
```python
from ai_stock_sentinel.analysis.metrics import ma, calc_bias, calc_rsi
```

移除原本的三個函式定義（`def calc_bias`、`def calc_rsi`、`def ma`），保留：
```python
# 保留私有別名，避免現有測試直接引用私有名稱時中斷
_calc_bias = calc_bias
_calc_rsi = calc_rsi
_ma = ma
```

**Step 4: 更新 `confidence_scorer.py`，移除 local import**

將 `derive_technical_score` 函式內（第 22 行）的 local import：
```python
# 在函式內 import，避免循環引用
from ai_stock_sentinel.analysis.context_generator import ma as calc_ma
```

改為在 **檔案頂部** import（與其他 import 並列）：
```python
from ai_stock_sentinel.analysis.metrics import ma as calc_ma
```

並移除函式內的 local import 行。

**Step 5: 執行測試確認**

```bash
cd backend
python -m pytest tests/ -x -q 2>&1 | tail -20
```
Expected: 所有測試 PASS，數量與 Step 1 相同

**Step 6: Commit**

```bash
cd backend
git add src/ai_stock_sentinel/analysis/metrics.py \
        src/ai_stock_sentinel/analysis/context_generator.py \
        src/ai_stock_sentinel/analysis/confidence_scorer.py
git commit -m "refactor: extract pure math utils to analysis/metrics.py to resolve circular import"
```

---

## Task 2: 將 `LangChainStockAnalyzer._parse_analysis` 改用 `JsonOutputParser`

**背景：** `langchain_analyzer.py:227-251` 手動剝除 markdown fence 再 `json.loads`。LangChain 的 `JsonOutputParser` 已內建這個邏輯，且能更優雅地處理 LLM 不確定性輸出。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`
- Test: `backend/tests/analysis/test_langchain_analyzer.py`（確認現有 `_parse_analysis` 測試仍覆蓋）

**Step 1: 確認現有 analyzer 測試通過**

```bash
cd backend
python -m pytest tests/analysis/test_langchain_analyzer.py -v 2>&1 | tail -20
```
Expected: PASS（記錄測試數量）

**Step 2: 修改 `analyze` 方法，把 chain 末端加上 `JsonOutputParser`**

在 `analyze` 方法（約第 179 行）的 import 區段，新增取得 `JsonOutputParser`：

```python
output_parsers = import_module("langchain_core.output_parsers")
prompts = import_module("langchain_core.prompts")
JsonOutputParser = getattr(output_parsers, "JsonOutputParser")
ChatPromptTemplate = getattr(prompts, "ChatPromptTemplate")
```

將 chain 定義從：
```python
chain = prompt | self.llm | StrOutputParser()
raw = chain.invoke(...)
return self._parse_analysis(raw)
```

改為：
```python
chain = prompt | self.llm | JsonOutputParser()
try:
    data = chain.invoke({...})  # data 已是 dict
    return self._parse_analysis_from_dict(data)
except Exception:
    # Fallback: 若 JsonOutputParser 失敗，改用原始字串解析
    str_chain = prompt | self.llm | StrOutputParser()
    raw = str_chain.invoke({...})
    return self._parse_analysis(raw)
```

**Step 3: 新增 `_parse_analysis_from_dict` 靜態方法**

在 `_parse_analysis` 方法下方新增：

```python
@staticmethod
def _parse_analysis_from_dict(data: dict) -> AnalysisDetail:
    """從已解析的 dict 建立 AnalysisDetail（JsonOutputParser 路徑）。"""
    return AnalysisDetail(
        summary=str(data.get("summary", "")),
        risks=[str(r) for r in data.get("risks", [])[:3]],
        technical_signal=str(data.get("technical_signal", "sideways")),
        institutional_flow=data.get("institutional_flow") or None,
        sentiment_label=data.get("sentiment_label") or None,
        tech_insight=data.get("tech_insight") or None,
        inst_insight=data.get("inst_insight") or None,
        news_insight=data.get("news_insight") or None,
        final_verdict=data.get("final_verdict") or None,
        fundamental_insight=data.get("fundamental_insight") or None,
    )
```

**Step 4: 執行測試**

```bash
cd backend
python -m pytest tests/analysis/test_langchain_analyzer.py -v 2>&1 | tail -30
```
Expected: 所有測試 PASS

**Step 5: Commit**

```bash
cd backend
git add src/ai_stock_sentinel/analysis/langchain_analyzer.py
git commit -m "refactor: use JsonOutputParser in LangChainStockAnalyzer with str fallback"
```

---

## Task 3: `GraphState` 分組為子 TypedDict

**背景：** `graph/state.py` 目前是一個 60 行的平鋪 TypedDict。依功能分組可減少每個節點的心理負擔。分組方式：`MarketDataState`（快照/技術）、`AnalysisState`（LLM 分析結果）、`PositionState`（持倉診斷）、`NewsState`（新聞相關）。

**重要限制：** LangGraph 的 `GraphState` **必須維持單一 TypedDict** 才能被 graph builder 使用。分組的做法是定義 sub-TypedDict 作為類型文件用途，而 `GraphState` 繼承（或包含）這些分組的所有欄位。這樣 IDE 可以跳至子 TypedDict 查看欄位說明，而 LangGraph 仍看到平鋪結構。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/state.py`

**Step 1: 確認 graph 相關測試通過**

```bash
cd backend
python -m pytest tests/ -k "graph or state or node" -v 2>&1 | tail -30
```
Expected: PASS

**Step 2: 重構 `state.py`，加入分組 TypedDict**

將 `state.py` 改寫為以下結構（**GraphState 欄位清單不變**，只是新增分組說明）：

```python
from __future__ import annotations

from typing import Any, TypedDict

from ai_stock_sentinel.models import AnalysisDetail


# ── 欄位分組（類型說明用，非 LangGraph 節點狀態） ──────────────────────────

class _NewsStateFields(TypedDict, total=False):
    """新聞相關欄位"""
    news_content: str | None
    cleaned_news: dict[str, Any] | None
    raw_news_items: list[dict[str, Any]] | None
    cleaned_news_quality: dict[str, Any] | None
    news_display: dict[str, Any] | None
    news_display_items: list[dict[str, Any]]


class _MarketDataStateFields(TypedDict, total=False):
    """市場數據與技術分析欄位"""
    snapshot: dict[str, Any] | None
    institutional_flow: dict[str, Any] | None
    fundamental_data: dict[str, Any] | None
    technical_context: str | None
    institutional_context: str | None
    fundamental_context: str | None
    high_20d: float | None
    low_20d: float | None
    support_20d: float | None
    resistance_20d: float | None
    rsi14: float | None


class _AnalysisStateFields(TypedDict, total=False):
    """LLM 分析結果與信心分數欄位"""
    analysis: str | None
    analysis_detail: AnalysisDetail | None
    confidence_score: int | None
    cross_validation_note: str | None
    signal_confidence: int | None
    data_confidence: int | None
    strategy_type: str | None
    entry_zone: str | None
    stop_loss: str | None
    holding_period: str | None
    action_plan_tag: str | None
    action_plan: dict[str, Any] | None


class _PositionStateFields(TypedDict, total=False):
    """持倉診斷欄位（POST /analyze/position 使用）"""
    entry_price: float | None
    entry_date: str | None
    quantity: int | None
    profit_loss_pct: float | None
    cost_buffer_to_support: float | None
    position_status: str | None
    position_narrative: str | None
    trailing_stop: float | None
    trailing_stop_reason: str | None
    recommended_action: str | None
    exit_reason: str | None


# ── 實際 LangGraph 使用的狀態（平鋪，維持向後相容） ─────────────────────

class GraphState(TypedDict):
    # 基本控制欄位
    symbol: str
    data_sufficient: bool
    retry_count: int
    errors: list[dict[str, str]]
    requires_news_refresh: bool
    requires_fundamental_update: bool

    # 新聞相關（參見 _NewsStateFields）
    news_content: str | None
    cleaned_news: dict[str, Any] | None
    raw_news_items: list[dict[str, Any]] | None
    cleaned_news_quality: dict[str, Any] | None
    news_display: dict[str, Any] | None
    news_display_items: list[dict[str, Any]]

    # 市場數據（參見 _MarketDataStateFields）
    snapshot: dict[str, Any] | None
    institutional_flow: dict[str, Any] | None
    fundamental_data: dict[str, Any] | None
    technical_context: str | None
    institutional_context: str | None
    fundamental_context: str | None
    high_20d: float | None
    low_20d: float | None
    support_20d: float | None
    resistance_20d: float | None
    rsi14: float | None

    # 分析結果（參見 _AnalysisStateFields）
    analysis: str | None
    analysis_detail: AnalysisDetail | None
    confidence_score: int | None
    cross_validation_note: str | None
    signal_confidence: int | None
    data_confidence: int | None
    strategy_type: str | None
    entry_zone: str | None
    stop_loss: str | None
    holding_period: str | None
    action_plan_tag: str | None
    action_plan: dict[str, Any] | None

    # 持倉診斷（參見 _PositionStateFields）
    entry_price: float | None
    entry_date: str | None
    quantity: int | None
    profit_loss_pct: float | None
    cost_buffer_to_support: float | None
    position_status: str | None
    position_narrative: str | None
    trailing_stop: float | None
    trailing_stop_reason: str | None
    recommended_action: str | None
    exit_reason: str | None
```

**Step 3: 執行全套測試**

```bash
cd backend
python -m pytest tests/ -x -q 2>&1 | tail -20
```
Expected: 所有測試 PASS（欄位清單不變，不應有測試失敗）

**Step 4: Commit**

```bash
cd backend
git add src/ai_stock_sentinel/graph/state.py
git commit -m "refactor: group GraphState fields into documented sub-TypedDicts for maintainability"
```

---

## Task 4: `RssNewsClient` 加入錯誤日誌與 HTTP 錯誤碼記錄

**背景：** `rss_news_client.py` 在 `httpx.HTTPError` 時靜默回傳空列表，在 `ET.ParseError` 時也靜默回傳空列表。根據 PR 建議，應記錄具體錯誤碼/訊息，方便診斷網路波動。

**Files:**
- Modify: `backend/src/ai_stock_sentinel/data_sources/rss_news_client.py`

**Step 1: 確認現有 RSS 測試通過**

```bash
cd backend
python -m pytest tests/ -k "rss or news_client" -v 2>&1 | tail -20
```
Expected: PASS

**Step 2: 在 `rss_news_client.py` 頂部加入 logging**

在 import 區段新增：
```python
import logging

logger = logging.getLogger(__name__)
```

**Step 3: 在 `fetch_news` 的 except 區段記錄錯誤**

將：
```python
        except httpx.HTTPError:
            return []
```
改為：
```python
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "RSS fetch HTTP error: status=%s url=%s",
                exc.response.status_code,
                url,
            )
            return []
        except httpx.HTTPError as exc:
            logger.warning("RSS fetch network error: %s url=%s", exc, url)
            return []
```

**Step 4: 在 `_parse_rss` 的 except 區段記錄錯誤**

將：
```python
        except ET.ParseError:
            return []
```
改為：
```python
        except ET.ParseError as exc:
            logger.warning("RSS XML parse error: %s", exc)
            return []
```

**Step 5: 執行測試**

```bash
cd backend
python -m pytest tests/ -k "rss or news_client" -v 2>&1 | tail -20
```
Expected: PASS（現有測試 mock httpx，行為不變）

**Step 6: Commit**

```bash
cd backend
git add src/ai_stock_sentinel/data_sources/rss_news_client.py
git commit -m "fix: add structured logging to RssNewsClient for HTTP and XML parse errors"
```

---

## Task 5: 將關鍵 `print` 替換為 `logging`

**背景：** 系統中有 `print` 與 `json.dumps` 用於輸出結果，不利於線上排錯。本任務搜尋並替換主要業務流程中的 `print`，引入分層 logging（`INFO`、`WARNING`、`ERROR`）。

**注意：** 只替換業務邏輯中的 `print`（`graph/nodes.py`、`api.py`、`main.py`），不處理 CLI 輸出用的 `print`（如果有的話）。

**Files:**
- Modify: 視 grep 結果而定（主要是 `backend/src/ai_stock_sentinel/` 下的 `.py` 檔）

**Step 1: 搜尋所有 `print(` 呼叫**

```bash
cd backend
grep -rn "print(" src/ai_stock_sentinel/ --include="*.py" | grep -v "__pycache__"
```
記錄輸出，確認哪些檔案有 `print`。

**Step 2: 對每個有 `print` 的業務邏輯檔，加入 logger**

在每個檔案頂部加入（若尚未有）：
```python
import logging

logger = logging.getLogger(__name__)
```

**Step 3: 替換規則**

| 原始 `print` 用途 | 替換為 |
|---|---|
| 正常流程資訊（分析開始、完成） | `logger.info(...)` |
| 資料缺失、fallback 觸發 | `logger.warning(...)` |
| 例外捕獲訊息 | `logger.error(..., exc_info=True)` |
| 除錯用 json.dumps 輸出 | `logger.debug(...)` |

範例替換（`nodes.py` 中如果有）：
```python
# Before
print(f"[score_node] confidence_score={confidence_score}")

# After
logger.debug("score_node: confidence_score=%s", confidence_score)
```

**Step 4: 在應用程式入口設定 logging（`main.py` 或 `api.py`）**

若 `main.py` 或 `api.py` 尚未設定 logging，加入：
```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
```

**Step 5: 執行全套測試**

```bash
cd backend
python -m pytest tests/ -x -q 2>&1 | tail -20
```
Expected: 所有測試 PASS

**Step 6: Commit**

```bash
cd backend
git add -u
git commit -m "refactor: replace print statements with structured logging across business logic"
```

---

## 執行順序建議

1. **Task 1**（循環引用修復）— 最優先，建立乾淨的依賴基礎
2. **Task 4**（RSS 容錯日誌）— 獨立，風險最低
3. **Task 5**（全域 logging）— 依賴 Task 4 的 logging 模式為參考
4. **Task 3**（GraphState 分組）— 純文件性重構，不改邏輯
5. **Task 2**（JsonOutputParser）— 依賴 LangChain 安裝，需手動驗證

## 已知限制與注意事項

- **Task 2** 的 `JsonOutputParser` 路徑需要 `langchain_core` 已安裝。若環境中未安裝，fallback 到原有 `_parse_analysis` 即可。
- **Task 3** 不改 `GraphState` 的實際欄位清單，只是增加可讀性分組。若測試直接引用欄位名稱，不受影響。
- **Task 5** 執行前先跑 `grep` 確認 `print` 的實際位置，不要猜測。
