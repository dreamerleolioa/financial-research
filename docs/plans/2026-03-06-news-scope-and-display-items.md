# 計劃：消息面職責邊界釐清 + 多筆新聞列表（News Scope & Display Items）

> 日期：2026-03-06
> 狀態：已完成（2026-03-06）
> 目的：
>   1. 落地「消息面 ≠ 財報」的職責邊界——新聞僅貢獻市場情緒訊號，所有財務數字（EPS/營收/毛利率）須另從財報來源取得
>   2. 將 `news_display`（單筆）升級為 `news_display_items`（最多 5 筆陣列），讓前端可展示多筆近期新聞連結
> 追蹤文件：`docs/progress-tracker.md` → 「下一輪修正（新聞顯示多筆升級）」
> 原則：完成即補測試；每個 Task 需可獨立 commit

---

## 背景說明

### 變更一：消息面職責邊界釐清

`ai-stock-sentinel-architecture-spec.md` 原先描述「去情緒化後的事實型新聞摘要」，措辭含糊，隱含「新聞可提供財務數字」的誤解。
2026-03-05 已更新規格，明確定義：

- **消息面（News）**：聚焦影響市場情緒的事件訊號（法說會、政策、產業動態、法人評等調整等）
- **基本面（Fundamentals）**：財務數字（EPS、營收、毛利率）來自財報，需另行取得，**目前尚未實作**
- `sentiment_label` 的判斷依據是事件本身的性質（政策利多/法人調降/供應鏈負面），**不依賴財務數字的有無**

此規格變更影響三處程式邏輯：
1. `FinancialNewsCleaner`：`mentioned_numbers` 的存在與否不應影響 `quality_score`（`NO_FINANCIAL_NUMBERS` flag 的計分權重應降至 0）
2. `langchain_analyzer.py` System Prompt：不應要求 LLM 嘗試從新聞提取財務數字
3. `quality_gate_node`：`quality_score` 計算邏輯對應調整

### 變更二：`news_display` → `news_display_items`（陣列）

原設計只取 `raw_news_items[0]` 單筆，使用者點進去只能看一則新聞。
前端需要展示多筆近期新聞連結，以提供更完整的消息面輸入來源。

**新規格**：
- `news_display_items: list[dict]`，最多 5 筆
- 每筆：`{ "title": str, "date": str | null, "source_url": str | null }`
- 日期正規化規則與原 `news_display` 相同（RFC 2822 → ISO 8601，unknown → null）
- `raw_news_items` 不足 5 筆時，取全部

---

## Session 1：釐清 `mentioned_numbers` 品質旗標的計分語義

> **目的**：`NO_FINANCIAL_NUMBERS` 旗標不應降低 `quality_score`，因新聞本來就不保證有財務數字。

### 範圍

**後端**：
- `analysis/news_cleaner.py`（或 `analysis/quality_gate.py`）：`NO_FINANCIAL_NUMBERS` 旗標的計分貢獻改為 0（保留旗標供追蹤，但不扣分）
- 補齊測試：無財務數字但事件語義清晰的新聞，`quality_score` 不應低於 60

### 詳細任務

#### T1-1：確認目前計分邏輯

**檔案**：`backend/src/ai_stock_sentinel/analysis/news_cleaner.py`（或 `quality_gate.py`）

先閱讀現有 `quality_score` 計算邏輯，確認 `NO_FINANCIAL_NUMBERS` 是否有扣分。

#### T1-2：撰寫失敗測試

在 `backend/tests/test_news_quality_gate.py`（或對應測試檔）補充：

```python
def test_quality_score_not_penalized_for_no_financial_numbers() -> None:
    """
    新聞標題事件語義清晰、日期有效，即使 mentioned_numbers 為空，
    quality_score 也不應低於 60。
    新聞僅負責市場情緒；財務數字缺席不代表品質低劣。
    """
    from ai_stock_sentinel.analysis.quality_gate import QualityGate  # 依實際模組調整
    gate = QualityGate()
    result = gate.evaluate({
        "title": "台積電宣布擴大日本熊本廠投資",
        "date": "2026-03-05",
        "mentioned_numbers": [],
        "sentiment_label": "positive",
    })
    assert result["quality_score"] >= 60
    assert "NO_FINANCIAL_NUMBERS" in result["quality_flags"]  # 旗標仍保留
```

#### T1-3：修改計分邏輯

找到 `NO_FINANCIAL_NUMBERS` 的扣分設定，將其貢獻改為 `0`（旗標保留，不扣分）。

> ⚠️ **注意與 `DATE_UNKNOWN` 的區別**：
> - `NO_FINANCIAL_NUMBERS` 是 `quality_score`（新聞清潔品質）的旗標，本 Session 修改的是這層
> - `DATE_UNKNOWN` 的 -3 懲罰作用在 `signal_confidence`（信心分數），由 `score_node` 的 rule-based Python 套用
> - 兩者屬不同層，**本 Session 不實作 `DATE_UNKNOWN` 懲罰**，那部分見 `docs/plans/2026-03-06-spec-gap-fix.md` Session 7

#### T1-4：確認通過

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_news_quality_gate.py -v
```

#### T1-5：Commit

```bash
git add backend/src/ai_stock_sentinel/analysis/ backend/tests/test_news_quality_gate.py
git commit -m "fix: NO_FINANCIAL_NUMBERS flag no longer penalizes quality_score

News dimension is responsible for market sentiment signals only.
Absence of financial numbers in news is expected behavior, not low quality."
```

---

## Session 2：調整 LLM System Prompt 移除財務數字提取要求

> **目的**：Skeptic Mode 步驟一不應要求 LLM 從新聞提取財務數字，應聚焦情緒與事件語義。

### 範圍

**後端**：`analysis/langchain_analyzer.py` — `_SYSTEM_PROMPT` 或 `_HUMAN_PROMPT` 中關於「從新聞提取數值」的描述

### 詳細任務

#### T2-1：確認目前 Prompt 內容

閱讀 `langchain_analyzer.py` 的 `_SYSTEM_PROMPT`，確認步驟一是否有要求提取財務數字。

#### T2-2：修改 Prompt

找到 Skeptic Mode 步驟一描述，更新措辭：

**改前（類似）**：
```
步驟一：從新聞提取關鍵數值與情感標籤（Sentiment）
```

**改後**：
```
步驟一：從新聞識別市場情緒訊號（sentiment_label：positive / negative / neutral）。
判斷依據為事件本身的性質（法說會動態、政策利多/利空、法人評等調整、供應鏈事件等），
不依賴財務數字的有無。若新聞中碰巧出現數字（如「年增 20%」），可作為輔助，
但新聞不是財報資料源，不應嘗試從中提取結構化財務數值（EPS、毛利率等）。
```

#### T2-3：測試（LLM mock 驗證 prompt 不含舊措辭）

在 `tests/test_langchain_analyzer.py` 補充：

```python
def test_system_prompt_does_not_require_financial_number_extraction() -> None:
    """System Prompt 不應要求 LLM 從新聞提取財務數字。"""
    from ai_stock_sentinel.analysis.langchain_analyzer import LangChainAnalyzer
    analyzer = LangChainAnalyzer.__new__(LangChainAnalyzer)
    prompt = analyzer._SYSTEM_PROMPT if hasattr(analyzer, "_SYSTEM_PROMPT") else ""
    # 確認沒有「提取數值」「EPS」「毛利率」等財報相關指令
    forbidden = ["提取關鍵數值", "EPS", "毛利率", "財報"]
    for term in forbidden:
        assert term not in prompt, f"System Prompt 不應包含財報相關指令：{term!r}"
```

#### T2-4：Commit

```bash
git add backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py backend/tests/test_langchain_analyzer.py
git commit -m "fix: remove financial number extraction from LLM system prompt

News dimension focuses on market sentiment signals (events, policy, ratings).
LLM should not attempt to extract EPS/revenue/margins from news RSS."
```

---

## Session 3：後端 `news_display_items`（陣列）

> **目的**：從單筆 `news_display` 升級為最多 5 筆的 `news_display_items` 陣列。

### 範圍

**後端**：
- `graph/state.py`：`GraphState` 欄位 `news_display` → `news_display_items`
- `graph/nodes.py`：`quality_gate_node` 產出邏輯改為迭代 `raw_news_items[:5]`
- `api.py`：`AnalyzeResponse` 欄位更新，`initial_state` 更新

### 詳細任務

#### T3-1：撰寫失敗測試（state 欄位）

在 `tests/test_graph_state.py` 補充：

```python
def test_graph_state_has_news_display_items_field() -> None:
    """GraphState 應有 news_display_items 欄位（list 型別）。"""
    import typing
    from ai_stock_sentinel.graph.state import GraphState
    hints = typing.get_type_hints(GraphState)
    assert "news_display_items" in hints
```

#### T3-2：撰寫失敗測試（node 輸出）

在 `tests/test_graph_nodes.py` 補充：

```python
def test_quality_gate_node_produces_news_display_items_list() -> None:
    """quality_gate_node 應產出 news_display_items 陣列（最多 5 筆）。"""
    raw_items = [
        asdict(_make_raw_news_item(
            title=f"新聞標題 {i}",
            url=f"https://example.com/news/{i}",
            published_at="Mon, 03 Mar 2026 08:00:00 GMT",
        ))
        for i in range(1, 7)  # 6 筆，應截斷為 5
    ]
    state = _base_state(
        cleaned_news={
            "date": "Mon, 03 Mar 2026 08:00:00 GMT",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=raw_items,
    )
    result = quality_gate_node(state)

    items = result["news_display_items"]
    assert isinstance(items, list)
    assert len(items) == 5  # 最多 5 筆
    assert items[0]["title"] == "新聞標題 1"
    assert items[0]["date"] == "2026-03-03"
    assert items[0]["source_url"] == "https://example.com/news/1"


def test_quality_gate_node_news_display_items_empty_when_no_raw() -> None:
    """raw_news_items 為空時，news_display_items 應為空陣列。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=[],
    )
    result = quality_gate_node(state)
    assert result["news_display_items"] == []
```

#### T3-3：修改 `GraphState`

**檔案**：`backend/src/ai_stock_sentinel/graph/state.py`

```python
# 移除（若存在）：
news_display: dict[str, Any] | None

# 新增：
news_display_items: list[dict[str, Any]]
```

> 若 `news_display` 舊欄位不存在可跳過移除步驟。

#### T3-4：修改 `quality_gate_node`

**檔案**：`backend/src/ai_stock_sentinel/graph/nodes.py`

將產出 `news_display` 的區塊改為：

```python
# 產出 news_display_items（最多 5 筆，供前端顯示近期新聞連結）
news_display_items: list[dict[str, Any]] = []
raw_items = state.get("raw_news_items") or []
for raw_item in raw_items[:5]:
    item_date_str = raw_item.get("published_at") or raw_item.get("pub_date") or "unknown"
    item_date_result = QualityGate.normalize_date(item_date_str)
    normalized_item_date: str | None = (
        item_date_result.date if item_date_result.date != "unknown" else None
    )
    news_display_items.append({
        "title": raw_item.get("title", ""),
        "date": normalized_item_date,
        "source_url": raw_item.get("url") or None,
    })

result["news_display_items"] = news_display_items
```

> 若 `raw_news_items` 每筆的日期欄位名稱不同（`pub_date` / `published_at`），依實際欄位調整。

#### T3-5：修改 `api.py`

**`AnalyzeResponse`**：
```python
# 移除（若存在）：
news_display: dict[str, Any] | None = None

# 新增：
news_display_items: list[dict[str, Any]] = Field(default_factory=list)
```

**`initial_state`**：
```python
# 移除（若存在）：
"news_display": None,

# 新增：
"news_display_items": [],
```

**`return AnalyzeResponse(...)`**：
```python
# 移除（若存在）：
news_display=result.get("news_display"),

# 新增：
news_display_items=result.get("news_display_items") or [],
```

#### T3-6：確認通過

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_graph_state.py tests/test_graph_nodes.py tests/test_api.py -v
```

#### T3-7：Commit

```bash
git add backend/src/ai_stock_sentinel/graph/state.py \
        backend/src/ai_stock_sentinel/graph/nodes.py \
        backend/src/ai_stock_sentinel/api.py \
        backend/tests/
git commit -m "feat: news_display_items array (up to 5) replaces single news_display

Allows frontend to render a list of recent news links for users to browse.
Each item: { title, date (ISO 8601 | null), source_url | null }"
```

---

## Session 4：前端「近期新聞列表」元件

> **目的**：渲染多筆近期新聞，每筆標題可點擊開新分頁。

### 範圍

**前端**：`frontend/src/App.tsx`（或拆出獨立元件）

### 詳細任務

#### T4-1：更新 TypeScript interface

```typescript
interface NewsDisplayItem {
  title: string
  date: string | null
  source_url: string | null
}

// 在 AnalyzeResponse 中：
// 移除（若存在）：news_display: NewsDisplay | null
// 新增：
news_display_items: NewsDisplayItem[]
```

#### T4-2：更新 error fallback state

在 `catch` block 的 `setResult({...})` 中：
```typescript
// 移除（若存在）：news_display: null,
// 新增：
news_display_items: [],
```

#### T4-3：更新新聞卡片渲染

將原本單筆 `news_display` 渲染改為列表：

```tsx
<article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
  <div className="flex items-center justify-between">
    <h2 className="text-sm font-semibold text-slate-800">近期新聞</h2>
    {result?.cleaned_news?.sentiment_label && (
      <span
        className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${
          SENTIMENT_CLASS[result.cleaned_news.sentiment_label] ?? SENTIMENT_CLASS.neutral
        }`}
      >
        {SENTIMENT_LABEL[result.cleaned_news.sentiment_label] ?? '中性'}
      </span>
    )}
  </div>

  {result?.cleaned_news_quality != null &&
    (result.cleaned_news_quality.quality_score < 60 ||
      result.cleaned_news_quality.quality_flags.length > 0) && (
      <p className="mt-2 rounded-md bg-slate-100 px-3 py-1.5 text-xs text-slate-500">
        摘要品質受限
      </p>
    )}

  {result ? (
    result.news_display_items.length > 0 ? (
      <ul className="mt-3 divide-y divide-slate-100">
        {result.news_display_items.map((item, idx) => (
          <li key={idx} className="py-2.5">
            {item.source_url ? (
              <a
                href={item.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-sm text-slate-800 hover:text-indigo-600 hover:underline"
              >
                {item.title}
              </a>
            ) : (
              <p className="text-sm text-slate-800">{item.title}</p>
            )}
            {item.date && (
              <p className="mt-0.5 text-xs text-slate-400">{item.date}</p>
            )}
          </li>
        ))}
      </ul>
    ) : (
      <p className="mt-3 text-sm text-slate-400">本次無新聞資料。</p>
    )
  ) : (
    <p className="mt-3 text-sm text-slate-400">請先執行分析。</p>
  )}

  <p className="mt-3 text-xs text-slate-400">
    以上為市場情緒參考新聞。財報數字請參閱
    <a
      href="https://mops.twse.com.tw"
      target="_blank"
      rel="noopener noreferrer"
      className="ml-0.5 text-indigo-500 hover:underline"
    >
      公開資訊觀測站
    </a>。
  </p>
</article>
```

#### T4-4：清理舊程式碼

移除已無用的：
- `NewsDisplay` interface（若存在）
- `cleanedNewsView` useMemo（若新聞卡片已改讀 `news_display_items`）

#### T4-5：手動驗證

啟動前後端，輸入 `2330.TW`，確認：
- 新聞卡片顯示多筆（最多 5 筆）近期新聞
- 每筆標題可點擊，開新分頁
- 發布日期正確（ISO 格式或不顯示）
- 整體情緒 badge 顯示於卡片標題旁
- 底部有公開資訊觀測站提示連結

#### T4-6：Commit

```bash
git add frontend/src/App.tsx
git commit -m "feat: news card renders up to 5 recent news items with source links

Each news item shows clickable title (opens new tab) and publish date.
Sentiment badge shows overall market sentiment from cleaned_news.
Footer note clarifies news is for sentiment reference, not financial data."
```

---

## Session 5：全套回歸測試 + 進度更新

#### T5-1：跑完整測試

```bash
cd backend
PYTHONPATH=src ./venv/bin/pytest -v
```

Expected: 全部 PASS（基線為前次完成後的測試數，加上本計劃新增約 6~8 個）

#### T5-2：更新 `progress-tracker.md`

在「下一輪修正（新聞顯示多筆升級）」區塊，將各 NM-* 項目標記為 ✅。

#### T5-3：Spec Review

對照 `docs/specs/ai-stock-sentinel-architecture-spec.md` 與 `docs/progress-tracker.md`，確認本計劃所有變更：

1. 與架構規格文件的描述一致（消息面職責邊界、新聞欄位結構）
2. 未引入新的規格缺口
3. 若發現新缺口，補記至 `progress-tracker.md` 的「待優化缺口」區塊，並決定是否需新建計劃文件

---

## 進度追蹤（progress-tracker.md 待補項目）

在 `progress-tracker.md` 新增以下 section（待執行後逐一打勾）：

```markdown
### 下一輪修正（消息面職責邊界 + 多筆新聞列表）

> 計劃文件：`docs/plans/2026-03-06-news-scope-and-display-items.md`

- [ ] NM-1：`NO_FINANCIAL_NUMBERS` flag 計分貢獻改為 0，旗標保留但不扣 quality_score
- [ ] NM-2：LLM System Prompt 移除「從新聞提取財務數字」要求，改為聚焦事件情緒語義
- [ ] NM-3：`GraphState` `news_display` → `news_display_items: list[dict]`
- [ ] NM-4：`quality_gate_node` 迭代 `raw_news_items[:5]`，產出 `news_display_items` 陣列
- [ ] NM-5：`api.py` `AnalyzeResponse` 欄位更新（`news_display_items`）
- [ ] NM-6：前端新聞卡片改為多筆列表，每筆可點擊連結 + 公開資訊觀測站提示
- [ ] NM-7：補齊測試（state 欄位、node 輸出、API 欄位、quality_score 計分）
```
