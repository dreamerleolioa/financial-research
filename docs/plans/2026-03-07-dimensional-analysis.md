# 分維度拆解分析（Dimensional Analysis）實作計劃

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 將 LLM 分析輸出從單一 `summary` 段落升級為三維獨立分析小卡（技術面 / 籌碼面 / 消息面）+ 綜合仲裁，提升可讀性與可解釋性。

**Architecture:** 後端在 `AnalysisDetail` dataclass 新增四個欄位（`tech_insight` / `inst_insight` / `news_insight` / `final_verdict`），並更新 LLM System Prompt 強制分段輸出、禁止跨維度混寫；前端將現有「分析報告」單一卡片改為三張維度小卡 + 一張全寬綜合仲裁卡。架構不動（不新增 LangGraph 節點），所有改動集中在 `models.py`、`langchain_analyzer.py`、`App.tsx`。

**Tech Stack:** Python dataclass、LangChain ChatPromptTemplate、React + TypeScript + Tailwind CSS

---

## 背景知識（給零脈絡的工程師）

### 專案結構

```
backend/src/ai_stock_sentinel/
  models.py                        # AnalysisDetail dataclass ← 任務 A 修改
  analysis/
    langchain_analyzer.py          # LLM prompt + _parse_analysis() ← 任務 B 修改
    interface.py                   # StockAnalyzer Protocol（不需改動）
  graph/
    nodes.py                       # analyze_node（不需改動）
frontend/src/
  App.tsx                          # 全部前端 UI ← 任務 C 修改
backend/tests/
  test_langchain_analyzer.py       # 主要測試目標
  test_api.py                      # API 整合測試
```

### 現況

- `AnalysisDetail` 目前有：`summary`、`risks`、`technical_signal`、`institutional_flow`、`sentiment_label`
- `_SYSTEM_PROMPT` 要求 LLM 輸出含 `summary`、`risks`、`technical_signal`、`institutional_flow`、`sentiment_label` 的 JSON
- `_parse_analysis()` 從 JSON 解析上述欄位，失敗時 fallback 為純文字 `summary`
- 前端「分析報告」卡片讀取 `analysis_detail.summary`、`risks`、`technical_signal`，單一文字方塊呈現

### 目標

新增四個欄位，LLM 分段輸出、前端分卡顯示：

| 欄位 | 職責 | 跨維度限制 |
|------|------|-----------|
| `tech_insight` | 均線排列、RSI、支撐壓力位 | 禁止提及法人買賣超或新聞事件 |
| `inst_insight` | 三大法人買賣超、融資券 | 禁止提及均線數值、RSI、新聞事件 |
| `news_insight` | 市場情緒、事件性質、時效性 | 禁止提及具體技術指標數值 |
| `final_verdict` | 三維整合仲裁，解釋信心分數 | 允許跨維度推論 |

### 執行指令

```bash
cd backend
make test          # 執行全套測試（目前 295 passed）
```

---

## 任務 A：`AnalysisDetail` 新增四個欄位

### Task A-1：新增欄位至 dataclass

**Files:**
- Modify: `backend/src/ai_stock_sentinel/models.py`

**Step 1: 寫失敗測試**

在 `backend/tests/test_langchain_analyzer.py` 末尾新增：

```python
# ---------------------------------------------------------------------------
# Session 8: Dimensional analysis fields
# ---------------------------------------------------------------------------

def test_analysis_detail_has_dimensional_fields():
    """AnalysisDetail 應包含四個分維度欄位，預設 None。"""
    from ai_stock_sentinel.models import AnalysisDetail
    detail = AnalysisDetail(summary="摘要")
    assert hasattr(detail, "tech_insight")
    assert hasattr(detail, "inst_insight")
    assert hasattr(detail, "news_insight")
    assert hasattr(detail, "final_verdict")
    assert detail.tech_insight is None
    assert detail.inst_insight is None
    assert detail.news_insight is None
    assert detail.final_verdict is None


def test_analysis_detail_accepts_dimensional_field_values():
    """AnalysisDetail 應能接受四個分維度欄位的字串值。"""
    from ai_stock_sentinel.models import AnalysisDetail
    detail = AnalysisDetail(
        summary="摘要",
        tech_insight="均線多頭排列",
        inst_insight="外資連買",
        news_insight="法說會利多",
        final_verdict="三維共振",
    )
    assert detail.tech_insight == "均線多頭排列"
    assert detail.inst_insight == "外資連買"
    assert detail.news_insight == "法說會利多"
    assert detail.final_verdict == "三維共振"
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py::test_analysis_detail_has_dimensional_fields -v
```

預期：`FAILED` — `AttributeError: 'AnalysisDetail' object has no attribute 'tech_insight'`

**Step 3: 修改 `models.py`**

在 `institutional_flow: str | None = None` 後面加入：

```python
tech_insight: str | None = None
inst_insight: str | None = None
news_insight: str | None = None
final_verdict: str | None = None
```

完整 `AnalysisDetail` 結果：

```python
@dataclass
class AnalysisDetail:
    summary: str
    risks: list[str] = field(default_factory=list)
    technical_signal: Literal["bullish", "bearish", "sideways"] = "sideways"
    institutional_flow: str | None = None
    sentiment_label: str | None = None
    tech_insight: str | None = None
    inst_insight: str | None = None
    news_insight: str | None = None
    final_verdict: str | None = None
```

**Step 4: 執行測試，確認通過**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py::test_analysis_detail_has_dimensional_fields tests/test_langchain_analyzer.py::test_analysis_detail_accepts_dimensional_field_values -v
```

預期：2 passed

**Step 5: 執行全套確認無回歸**

```bash
cd backend && make test
```

預期：>= 295 passed，0 failed

**Step 6: Commit**

```bash
git add backend/src/ai_stock_sentinel/models.py backend/tests/test_langchain_analyzer.py
git commit -m "feat: add dimensional insight fields to AnalysisDetail"
```

---

## 任務 B：LLM Prompt 強制分維度輸出

### Task B-1：`_parse_analysis()` 支援新欄位

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`

**Step 1: 寫失敗測試**

在 `backend/tests/test_langchain_analyzer.py` 末尾新增：

```python
def test_parse_analysis_reads_dimensional_fields():
    """_parse_analysis 能正確讀取四個分維度欄位。"""
    raw = json.dumps({
        "summary": "綜合摘要",
        "risks": [],
        "technical_signal": "bullish",
        "tech_insight": "均線多頭排列，RSI 62 健康。",
        "inst_insight": "外資連買 3 日，籌碼沉澱。",
        "news_insight": "法說會利多，情緒正面。",
        "final_verdict": "三維共振，信心偏高。",
    })
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.tech_insight == "均線多頭排列，RSI 62 健康。"
    assert result.inst_insight == "外資連買 3 日，籌碼沉澱。"
    assert result.news_insight == "法說會利多，情緒正面。"
    assert result.final_verdict == "三維共振，信心偏高。"


def test_parse_analysis_dimensional_fields_none_when_absent():
    """_parse_analysis 在 LLM 未回傳分維度欄位時 fallback 為 None，不崩潰。"""
    raw = '{"summary": "ok", "risks": [], "technical_signal": "sideways"}'
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.tech_insight is None
    assert result.inst_insight is None
    assert result.news_insight is None
    assert result.final_verdict is None


def test_parse_analysis_empty_string_dimensional_fields_become_none():
    """_parse_analysis 回傳空字串分維度欄位時應轉換為 None。"""
    raw = json.dumps({
        "summary": "ok", "risks": [], "technical_signal": "sideways",
        "tech_insight": "", "inst_insight": "", "news_insight": "", "final_verdict": "",
    })
    result = LangChainStockAnalyzer._parse_analysis(raw)
    assert result.tech_insight is None
    assert result.inst_insight is None
    assert result.news_insight is None
    assert result.final_verdict is None
```

> 注意：測試頂部需要有 `import json`，請確認檔案第一行已有。若無，加在 `from __future__ import annotations` 後面。

**Step 2: 執行測試，確認失敗**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py::test_parse_analysis_reads_dimensional_fields -v
```

預期：`FAILED` — `assert result.tech_insight == ...`（欄位存在但 `_parse_analysis` 不讀取新欄位）

**Step 3: 修改 `_parse_analysis()` 讀取新欄位**

在 `langchain_analyzer.py` 的 `_parse_analysis()` 靜態方法中，`AnalysisDetail(...)` 建構時補入四個欄位：

```python
return AnalysisDetail(
    summary=str(data.get("summary", raw)),
    risks=[str(r) for r in data.get("risks", [])[:3]],
    technical_signal=str(data.get("technical_signal", "sideways")),
    institutional_flow=data.get("institutional_flow") or None,
    sentiment_label=data.get("sentiment_label") or None,
    tech_insight=data.get("tech_insight") or None,
    inst_insight=data.get("inst_insight") or None,
    news_insight=data.get("news_insight") or None,
    final_verdict=data.get("final_verdict") or None,
)
```

**Step 4: 執行測試，確認通過**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py::test_parse_analysis_reads_dimensional_fields tests/test_langchain_analyzer.py::test_parse_analysis_dimensional_fields_none_when_absent tests/test_langchain_analyzer.py::test_parse_analysis_empty_string_dimensional_fields_become_none -v
```

預期：3 passed

### Task B-2：更新 System Prompt 強制分維度輸出

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py`

**Step 1: 寫失敗測試**

```python
def test_system_prompt_contains_dimensional_section():
    """System Prompt 應包含分維度輸出指令。"""
    import ai_stock_sentinel.analysis.langchain_analyzer as mod
    prompt = mod._SYSTEM_PROMPT
    assert "tech_insight" in prompt, "System Prompt 應包含 tech_insight 欄位說明"
    assert "inst_insight" in prompt, "System Prompt 應包含 inst_insight 欄位說明"
    assert "news_insight" in prompt, "System Prompt 應包含 news_insight 欄位說明"
    assert "final_verdict" in prompt, "System Prompt 應包含 final_verdict 欄位說明"
    assert "禁止跨維度混寫" in prompt or "禁止混入" in prompt, \
        "System Prompt 應包含禁止跨維度混寫的限制"


def test_system_prompt_json_schema_includes_dimensional_fields():
    """System Prompt 的 JSON schema 範例應包含四個分維度欄位。"""
    import ai_stock_sentinel.analysis.langchain_analyzer as mod
    prompt = mod._SYSTEM_PROMPT
    for field in ["tech_insight", "inst_insight", "news_insight", "final_verdict"]:
        assert f'"{field}"' in prompt, f"JSON schema 缺少欄位：{field}"
```

**Step 2: 執行測試，確認失敗**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py::test_system_prompt_contains_dimensional_section -v
```

預期：`FAILED`

**Step 3: 更新 `_SYSTEM_PROMPT`**

將 `langchain_analyzer.py` 的 `_SYSTEM_PROMPT` 完整替換為以下內容（保留現有四步驟，新增分維度輸出要求）：

```python
_SYSTEM_PROMPT = """\
你是一位謹慎的台股研究助理，採用 Skeptic Mode（懷疑論模式）。
請嚴格按照以下四步驟進行分析，不得跳過任何步驟：

步驟一【識別市場情緒訊號】：從新聞識別 sentiment_label（positive / negative / neutral）。
判斷依據為事件本身的性質（法說會動態、政策利多/利空、法人評等調整、供應鏈事件等），
不依賴財務數字的有無。若新聞中碰巧出現百分比或金額數字，可作為輔助情緒佐證，
但新聞不是財務報告來源，不應嘗試從中整理結構化財務指標。
步驟二【對照】：將技術面訊號、籌碼面訊號、消息面情緒三方資料並列比較。
步驟三【衝突檢查】：明確指出三方資料中是否存在矛盾或異常；若有，提出具體衝突點。
步驟四【輸出】：只輸出有資料支撐的事實與推論，禁止補造未在輸入資料中出現的來源或數字。

分維度輸出規範（禁止跨維度混寫）：
- tech_insight：僅參考技術面資料（均線排列、RSI 位階、支撐壓力位）；禁止提及法人買賣超或新聞事件
- inst_insight：僅參考籌碼面資料（三大法人買賣超、融資券動向）；禁止提及均線數值、RSI、新聞事件
- news_insight：僅參考消息面資料（事件性質、市場情緒傾向）；禁止提及具體技術指標數值（如 RSI=62）
- final_verdict：整合三維訊號，解釋為何導向當前信心分數與策略；此段允許跨維度整合推論

規範：
- LLM 不得修改 confidence_score 或 cross_validation_note，這兩個欄位由 rule-based 計算已完成。
- 輸出格式：必須輸出合法 JSON，格式如下：
{{
  "summary": "2-3 句事實型摘要（可與 final_verdict 相同）",
  "risks": ["風險點 1", "風險點 2"],
  "technical_signal": "bullish|bearish|sideways",
  "institutional_flow": "從已提供的籌碼資料中讀取 flow_label，直接填入，不得修改",
  "sentiment_label": "從已提供的 cleaned_news 資料中讀取 sentiment_label，直接填入，不得修改",
  "tech_insight": "技術面獨立分析段落",
  "inst_insight": "籌碼面獨立分析段落",
  "news_insight": "消息面獨立分析段落",
  "final_verdict": "三維整合仲裁段落"
}}
- 不得輸出 JSON 以外的任何文字。
"""
```

**Step 4: 執行測試，確認通過**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py::test_system_prompt_contains_dimensional_section tests/test_langchain_analyzer.py::test_system_prompt_json_schema_includes_dimensional_fields -v
```

預期：2 passed

**Step 5: 確認原有 Prompt 相關測試不回歸**

```bash
cd backend && ./venv/bin/python -m pytest tests/test_langchain_analyzer.py -v
```

預期：全數 passed，特別確認 `test_system_prompt_does_not_require_financial_number_extraction` 和 `test_human_prompt_contains_news_summary_section` 仍通過。

**Step 6: 執行全套測試**

```bash
cd backend && make test
```

預期：>= 295 passed，0 failed

**Step 7: Commit**

```bash
git add backend/src/ai_stock_sentinel/analysis/langchain_analyzer.py backend/tests/test_langchain_analyzer.py
git commit -m "feat: update LLM prompt for dimensional analysis output"
```

---

## 任務 C：前端分維度小卡 UI

### Task C-1：更新 TypeScript interface + 新增維度燈號 map

**Files:**
- Modify: `frontend/src/App.tsx`

**Step 1: 更新 `AnalysisDetail` interface**

在 `App.tsx` 的 `AnalysisDetail` interface 新增四個欄位：

```typescript
interface AnalysisDetail {
  summary: string
  risks: string[]
  technical_signal: 'bullish' | 'bearish' | 'sideways'
  institutional_flow: string | null
  sentiment_label: string | null
  tech_insight: string | null
  inst_insight: string | null
  news_insight: string | null
  final_verdict: string | null
}
```

同時更新 `AnalyzeResponse` interface，補入新欄位（後端 API 已回傳 `analysis_detail` dict，前端 interface 需對齊）：

```typescript
interface AnalyzeResponse {
  // ... 現有欄位不變 ...
  data_confidence: number | null
  errors: ErrorDetail[]
}
```

> `data_confidence` 欄位已存在，確認 `AnalyzeResponse` 有包含即可，不需重複新增。

**Step 2: 新增籌碼面燈號 map**

在現有 `SIGNAL_LABEL` / `SIGNAL_CLASS` 下方新增：

```typescript
const INST_FLOW_BADGE: Record<string, { label: string; cls: string }> = {
  institutional_accumulation: { label: '法人買超', cls: 'bg-emerald-100 text-emerald-800' },
  distribution: { label: '主力出貨', cls: 'bg-red-100 text-red-800' },
  retail_chasing: { label: '散戶追高', cls: 'bg-orange-100 text-orange-800' },
  neutral: { label: '籌碼中性', cls: 'bg-slate-100 text-slate-700' },
}
```

### Task C-2：改版「分析報告」區塊為分維度小卡

**Files:**
- Modify: `frontend/src/App.tsx`

找到目前的「分析報告」section（約 line 377–409），完整替換為以下結構：

```tsx
<section className="space-y-4">
  <h2 className="text-sm font-semibold text-slate-800">分析報告</h2>

  {result ? (
    result.analysis_detail ? (
      <div className="space-y-4">
        {/* 三維小卡（3 欄網格） */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {/* 技術面卡片 */}
          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-slate-600">技術面</h3>
              <span
                className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${
                  SIGNAL_CLASS[result.analysis_detail.technical_signal] ?? SIGNAL_CLASS.sideways
                }`}
              >
                {SIGNAL_LABEL[result.analysis_detail.technical_signal] ?? '盤整'}
              </span>
            </div>
            <p className="text-sm text-slate-700 leading-relaxed">
              {result.analysis_detail.tech_insight ?? '（無技術面分析）'}
            </p>
          </article>

          {/* 籌碼面卡片 */}
          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-slate-600">籌碼面</h3>
              {result.analysis_detail.institutional_flow && INST_FLOW_BADGE[result.analysis_detail.institutional_flow] && (
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${
                    INST_FLOW_BADGE[result.analysis_detail.institutional_flow].cls
                  }`}
                >
                  {INST_FLOW_BADGE[result.analysis_detail.institutional_flow].label}
                </span>
              )}
            </div>
            <p className="text-sm text-slate-700 leading-relaxed">
              {result.analysis_detail.inst_insight ?? '（無籌碼面分析）'}
            </p>
          </article>

          {/* 消息面卡片 */}
          <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-slate-600">消息面</h3>
              {result.analysis_detail.sentiment_label && (
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${
                    SENTIMENT_CLASS[result.analysis_detail.sentiment_label] ?? SENTIMENT_CLASS.neutral
                  }`}
                >
                  {SENTIMENT_LABEL[result.analysis_detail.sentiment_label] ?? '中性'}
                </span>
              )}
            </div>
            <p className="text-sm text-slate-700 leading-relaxed">
              {result.analysis_detail.news_insight ?? '（無消息面分析）'}
            </p>
          </article>
        </div>

        {/* 綜合仲裁全寬卡 */}
        <article className="rounded-xl border border-indigo-100 bg-indigo-50 p-4 shadow-sm">
          <h3 className="text-xs font-semibold text-indigo-700 mb-2">綜合仲裁</h3>
          <p className="text-sm text-slate-700 leading-relaxed">
            {result.analysis_detail.final_verdict ?? result.analysis_detail.summary}
          </p>
          {result.analysis_detail.risks.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-medium text-slate-500 mb-1">風險提示</p>
              <ul className="list-disc list-inside space-y-1">
                {result.analysis_detail.risks.map((risk, i) => (
                  <li key={i} className="text-sm text-slate-700">{risk}</li>
                ))}
              </ul>
            </div>
          )}
        </article>
      </div>
    ) : result.analysis ? (
      <pre className="whitespace-pre-wrap wrap-break-word text-sm text-slate-700 leading-relaxed rounded-xl border border-slate-200 bg-white p-4">
        {result.analysis}
      </pre>
    ) : (
      <p className="text-sm text-slate-400">本次無分析結果。</p>
    )
  ) : (
    <p className="text-sm text-slate-400">請先執行分析。</p>
  )}
</section>
```

> **重要**：新的 section 不再有 `rounded-xl border ...` 包覆整個區塊（改由各子卡片各自帶邊框）。原 section 的外層 `<section className="rounded-xl border ...">` 改為 `<section className="space-y-4">`。

**Step 3: 更新 `handleAnalyze` 的 catch 區塊**

catch 區塊的 fallback 物件需補入新欄位（避免型別錯誤）。找到 `setResult({...})` 的 catch 區段，確認 `analysis_detail: null` 即可（interface 已宣告 nullable，不需額外填入）。

**Step 4: 手動驗收**

```bash
cd frontend && npm run dev
```

1. 輸入 `2330.TW` 執行分析
2. 確認「分析報告」區塊顯示三張維度小卡（技術面 / 籌碼面 / 消息面）
3. 確認各卡片標題旁有對應燈號 badge
4. 確認「綜合仲裁」全寬卡顯示 `final_verdict`（若 LLM 尚未回傳新欄位，fallback 顯示 `summary`）
5. 確認 `analysis_detail` 為 null 時仍 fallback 到純文字 `analysis`，不崩潰

**Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: dimensional analysis cards in frontend"
```

---

## 最終驗收

```bash
cd backend && make test
```

預期：>= 298 passed（+3 新增測試），0 failed

驗收清單：
- [ ] `AnalysisDetail` 含 `tech_insight` / `inst_insight` / `news_insight` / `final_verdict`，預設 None
- [ ] `_parse_analysis()` 正確讀取四個新欄位；空字串轉 None；欄位缺失 fallback None 不崩潰
- [ ] `_SYSTEM_PROMPT` 包含分維度輸出指令與禁止跨維度混寫限制
- [ ] 原有 prompt 測試（`test_system_prompt_does_not_require_financial_number_extraction`、`test_human_prompt_contains_news_summary_section`）仍通過
- [ ] 前端三張維度小卡正確渲染（含 null fallback 顯示佔位文字，不崩潰）
- [ ] `analysis_detail = null` 時前端仍 fallback 到純文字 `analysis`

---

## Spec Review（Session 8 結束後必做）

所有任務完成、測試通過後，對照架構規格文件確認無新缺口。

### 檢查清單

**1. `AnalyzeResponse` 頂層欄位**

目前四個新欄位藏在 `analysis_detail` dict 內（前端透過 `result.analysis_detail.tech_insight` 讀取）。確認此設計符合規格預期，**不需要**將四個欄位浮出 `AnalyzeResponse` 頂層。

對照依據：架構規格 §3.2「輸出結構」的 JSON 範例中，`tech_insight` 等欄位列於 `AnalysisDetail` 層級而非頂層。

若發現不一致，補記至 `progress-tracker.md` 待優化缺口。

**2. `GraphState` 欄位**

四個新欄位存在於 `AnalysisDetail` dataclass（已由 `analyze_node` 寫入 `state["analysis_detail"]`），不需在 `GraphState` 獨立列出。確認 `graph/state.py` 的 `analysis_detail` 欄位型別定義正確（`AnalysisDetail | None`）。

**3. 架構規格 §7 驗收標準**

確認 `docs/specs/ai-stock-sentinel-architecture-spec.md` §7 驗收標準是否需要補入分維度輸出的 DoD。若規格 §7 缺少對應條目，在規格文件中補上：

```
- **分維度分析**：LLM 輸出必須包含 `tech_insight`（技術面）、`inst_insight`（籌碼面）、`news_insight`（消息面）、`final_verdict`（綜合仲裁）四個獨立段落；各維度禁止跨維度混寫
```

**4. 未引入新缺口確認**

- [ ] `_parse_analysis()` 空字串轉 None 邏輯與現有 `institutional_flow`、`sentiment_label` 處理方式一致
- [ ] `AnalysisDetail` 向後相容：舊有 `summary` / `risks` / `technical_signal` 欄位不受影響
- [ ] 前端 `analysis_detail = null` 的 fallback 路徑仍完整（純文字 `analysis` 回退）
- [ ] 若發現新缺口，補記至 `docs/progress-tracker.md` 待優化缺口第 N 條

---

## Handoff Snapshot 模板

```markdown
## Handoff Snapshot — 2026-03-07 Session 8 結束

- 已完成（本 Session）：
  - 任務 A：AnalysisDetail 新增四個分維度欄位
  - 任務 B：LLM System Prompt 強制分段輸出
  - 任務 C：前端分維度小卡 UI

- 驗收證據：
  - make test → N passed
  - 瀏覽器手動驗收：分析報告顯示三維小卡 + 綜合仲裁卡
```
