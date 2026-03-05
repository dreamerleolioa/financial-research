# news_display 拆分計劃

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 將新聞資料拆為 `cleaned_news`（供 LLM pipeline）與 `news_display`（供前端顯示），讓前端顯示的標題/日期/來源對使用者可讀。

**Architecture:** `quality_gate_node` 在產出品質分數後，額外從 `raw_news_items[0]` 組出 `news_display`（RSS 原始乾淨欄位）並寫入 state。`cleaned_news` 繼續保留給 `score_node`/`strategy_node` 消費，不給前端直接顯示。前端改讀 `news_display` 渲染新聞卡片，移除 `mentioned_numbers` chips。

**Tech Stack:** Python/FastAPI（後端）、React/TypeScript（前端）、pytest

---

### Task 1：後端 state 新增 `news_display`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/state.py`

**Step 1: 寫失敗測試**

在 `backend/tests/test_graph_state.py` 加：

```python
def test_graph_state_has_news_display_field() -> None:
    """GraphState 必須包含 news_display 欄位。"""
    from ai_stock_sentinel.graph.state import GraphState
    import typing
    hints = typing.get_type_hints(GraphState)
    assert "news_display" in hints
```

**Step 2: 確認測試失敗**

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_graph_state.py::test_graph_state_has_news_display_field -v
```
Expected: FAIL — `AssertionError: assert 'news_display' in {...}`

**Step 3: 實作**

在 `state.py` 的 `GraphState` 加一行：

```python
news_display: dict[str, Any] | None
```

**Step 4: 確認通過**

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_graph_state.py -v
```

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/graph/state.py backend/tests/test_graph_state.py
git commit -m "feat: add news_display field to GraphState"
```

---

### Task 2：`quality_gate_node` 產出 `news_display`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/graph/nodes.py`
- Test: `backend/tests/test_graph_nodes.py`

`news_display` 欄位定義：
```python
{
    "title": str,        # raw_news_items[0]["title"]（RSS 原始標題）
    "date": str | None,  # QualityGate.normalize_date 後的 ISO 日期，unknown → None
    "source_url": str | None,  # raw_news_items[0]["url"]
}
```

**Step 1: 寫失敗測試**

在 `test_graph_nodes.py` 尾端加：

```python
def test_quality_gate_node_produces_news_display() -> None:
    """quality_gate_node 應產出 news_display，含乾淨標題、正規化日期、來源 URL。"""
    state = _base_state(
        cleaned_news={
            "date": "Mon, 03 Mar 2026 08:00:00 GMT",
            "title": "Wed, 04 Mar 2026 23:02:08 GMT",
            "mentioned_numbers": ["18.2%"],
            "sentiment_label": "neutral",
        },
        raw_news_items=[
            asdict(_make_raw_news_item(
                title="台積電 2 月營收年增 20%",
                url="https://example.com/news/1",
                published_at="Mon, 03 Mar 2026 08:00:00 GMT",
            ))
        ],
    )
    result = quality_gate_node(state)

    display = result["news_display"]
    assert display is not None
    assert display["title"] == "台積電 2 月營收年增 20%"
    assert display["date"] == "2026-03-03"   # RFC 2822 → ISO
    assert display["source_url"] == "https://example.com/news/1"


def test_quality_gate_node_news_display_date_none_when_unknown() -> None:
    """cleaned_news.date=unknown 時，news_display.date 應為 None。"""
    state = _base_state(
        cleaned_news={
            "date": "unknown",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=[asdict(_make_raw_news_item())],
    )
    result = quality_gate_node(state)

    assert result["news_display"]["date"] is None


def test_quality_gate_node_news_display_none_when_no_cleaned_news() -> None:
    """cleaned_news 為 None 時，news_display 也應為 None。"""
    state = _base_state(cleaned_news=None)
    result = quality_gate_node(state)

    assert result.get("news_display") is None


def test_quality_gate_node_news_display_none_when_no_raw_news_items() -> None:
    """raw_news_items 為空時，news_display 應為 None（無來源可取）。"""
    state = _base_state(
        cleaned_news={
            "date": "2026-03-03",
            "title": "台積電公告",
            "mentioned_numbers": [],
            "sentiment_label": "neutral",
        },
        raw_news_items=None,
    )
    result = quality_gate_node(state)

    assert result.get("news_display") is None
```

**Step 2: 確認失敗**

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_graph_nodes.py::test_quality_gate_node_produces_news_display tests/test_graph_nodes.py::test_quality_gate_node_news_display_date_none_when_unknown tests/test_graph_nodes.py::test_quality_gate_node_news_display_none_when_no_cleaned_news tests/test_graph_nodes.py::test_quality_gate_node_news_display_none_when_no_raw_news_items -v
```
Expected: FAIL — `KeyError: 'news_display'`

**Step 3: 實作**

在 `nodes.py` 的 `quality_gate_node` return 前加：

```python
# 產出 news_display（供前端顯示，不污染 cleaned_news）
news_display: dict[str, Any] | None = None
raw_items = state.get("raw_news_items") or []
if cleaned and raw_items:
    first_raw = raw_items[0]
    # 日期正規化：unknown → None
    normalized_date = date_result.date
    display_date: str | None = normalized_date if normalized_date != "unknown" else None
    news_display = {
        "title": first_raw.get("title", ""),
        "date": display_date,
        "source_url": first_raw.get("url") or None,
    }

result["news_display"] = news_display
```

注意：`date_result` 已在前面 `QualityGate.normalize_date(...)` 中計算，直接重用。

**Step 4: 確認通過**

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_graph_nodes.py -v
```
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/graph/nodes.py backend/tests/test_graph_nodes.py
git commit -m "feat: quality_gate_node produces news_display for frontend"
```

---

### Task 3：`api.py` 初始 state 及 response 加入 `news_display`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/api.py`
- Test: `backend/tests/test_api.py`

**Step 1: 寫失敗測試**

在 `test_api.py` 尾端加：

```python
def test_analyze_response_has_news_display_field() -> None:
    """AnalyzeResponse 必須包含 news_display 欄位（值可為 None）。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    assert "news_display" in response.json()


def test_analyze_response_news_display_contains_expected_fields() -> None:
    """news_display 非 None 時，應包含 title、date、source_url。"""
    graph = _make_graph({
        "snapshot": asdict(_SNAPSHOT),
        "analysis": "分析結果",
        "cleaned_news": None,
        "news_display": {
            "title": "台積電 Q1 法說會",
            "date": "2026-03-05",
            "source_url": "https://example.com/news/1",
        },
        "errors": [],
    })
    client = _client_with_graph(graph)

    response = client.post("/analyze", json={"symbol": "2330.TW"})

    assert response.status_code == 200
    body = response.json()
    display = body["news_display"]
    assert display["title"] == "台積電 Q1 法說會"
    assert display["date"] == "2026-03-05"
    assert display["source_url"] == "https://example.com/news/1"
```

**Step 2: 確認失敗**

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_api.py::test_analyze_response_has_news_display_field tests/test_api.py::test_analyze_response_news_display_contains_expected_fields -v
```
Expected: FAIL

**Step 3: 實作**

`api.py` 三處修改：

1. `AnalyzeResponse` 加欄位：
```python
news_display: dict[str, Any] | None = None
```

2. `initial_state` 加鍵：
```python
"news_display": None,
```

3. `return AnalyzeResponse(...)` 加參數：
```python
news_display=result.get("news_display"),
```

**Step 4: 確認通過**

```bash
PYTHONPATH=src ./venv/bin/pytest tests/test_api.py -v
```

**Step 5: Commit**

```bash
git add backend/src/ai_stock_sentinel/api.py backend/tests/test_api.py
git commit -m "feat: expose news_display in AnalyzeResponse"
```

---

### Task 4：前端改讀 `news_display`，移除 `mentioned_numbers` chips

**Files:**
- Modify: `frontend/src/App.tsx`

**Step 1: 在 `App.tsx` 新增 interface**

在 `CleanedNewsQuality` 之後加：

```typescript
interface NewsDisplay {
  title: string
  date: string | null
  source_url: string | null
}
```

**Step 2: 在 `AnalyzeResponse` 加欄位**

```typescript
news_display: NewsDisplay | null
```

**Step 3: 更新 error fallback**

在 `catch` 的 `setResult({...})` 加：
```typescript
news_display: null,
```

**Step 4: 更新新聞卡片渲染邏輯**

目前新聞卡片讀 `cleanedNewsView`（來自 `cleaned_news`）顯示標題和日期，改為：
- 標題、日期、來源連結 → 讀 `result.news_display`
- 情緒 badge → 繼續讀 `result.cleaned_news?.sentiment_label`
- 移除 `mentioned_numbers` chips 區塊

具體改法（找到 `<article>` 新聞重點摘要區塊，替換內容）：

```tsx
<article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:p-6">
  <h2 className="text-sm font-semibold text-slate-800">新聞重點摘要</h2>
  {result?.cleaned_news_quality != null &&
    (result.cleaned_news_quality.quality_score < 60 ||
      result.cleaned_news_quality.quality_flags.length > 0) && (
      <p className="mt-2 rounded-md bg-slate-100 px-3 py-1.5 text-xs text-slate-500">
        摘要品質受限
      </p>
    )}
  {result ? (
    result.news_display ? (
      <div className="mt-3 space-y-2 text-sm text-slate-700">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-slate-500">
            日期：{result.news_display.date ?? '未知'}
          </p>
          {result.cleaned_news && (
            <span
              className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                SENTIMENT_CLASS[
                  (result.cleaned_news.sentiment_label as string) ?? 'neutral'
                ] ?? SENTIMENT_CLASS.neutral
              }`}
            >
              {SENTIMENT_LABEL[
                (result.cleaned_news.sentiment_label as string) ?? 'neutral'
              ] ?? '中性'}
            </span>
          )}
        </div>
        <p className="leading-relaxed">{result.news_display.title}</p>
        {result.news_display.source_url && (
          <a
            href={result.news_display.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block text-xs text-indigo-600 hover:underline"
          >
            查看原文 →
          </a>
        )}
      </div>
    ) : (
      <p className="mt-3 text-sm text-slate-400">本次無新聞資料可萃取。</p>
    )
  ) : (
    <p className="mt-3 text-sm text-slate-400">請先執行分析。</p>
  )}
</article>
```

**Step 5: 清理已無用的 `cleanedNewsView` useMemo**

`cleanedNewsView` 及其相關的 `CleanedNewsView` interface 可刪除（前端不再需要）。

**Step 6: 手動驗證**

啟動前後端，輸入 `2330.TW` 查詢，確認：
- 新聞卡片顯示 RSS 原始標題（非時間戳）
- 日期正確（ISO 格式或「未知」）
- 「查看原文 →」連結可點擊
- 不再顯示 mentioned_numbers chips

**Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: news card reads news_display, remove mentioned_numbers chips"
```

---

### Task 5：跑完整測試確認無 regression

```bash
PYTHONPATH=src ./venv/bin/pytest -v
```

Expected: 全部 PASS（目前基線 198 tests，加上本 PR 新增後應為 207+）
