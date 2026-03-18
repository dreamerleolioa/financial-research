# P1 實作計劃：策略卡升級、盤中分流、回測持久化

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 Roadmap P1 三項工作：Analyze 頁策略卡四段式升級、盤中 vs 收盤策略 guardrail 強化、回測結果持久化至 DB。

**Architecture:** Task 1-2 為後端邏輯調整（strategy_generator.py guardrail + 新 DB schema），Task 3 為前端 UI 重構，Task 4 為回測腳本 DB 寫入。各 Task 相對獨立，可依序執行。

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, pytest, React + TypeScript, Tailwind CSS

**Preconditions:**
- P0 前置計劃已完成（`strategy_version` 欄位存在、`--mode new-position` 可執行）
- P0 回測腳本輸出 schema 已穩定

---

## Task 1：盤中 suggested_position_size guardrail

對應 spec：`docs/specs/p1-intraday-vs-close-split-spec.md`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/analysis/strategy_generator.py`
- Modify: `backend/tests/test_strategy_generator.py`

### Step 1：確認現有 `_determine_conviction_level` 邏輯

```bash
grep -n "is_final" backend/src/ai_stock_sentinel/analysis/strategy_generator.py
```

預期找到盤中降級那段（`is_final=False` → `high` 改 `medium`）。確認存在後繼續。

### Step 2：在測試檔確認現有 is_final 測試覆蓋

```bash
grep -n "is_final" backend/tests/test_strategy_generator.py
```

若無相關測試，Step 3 先補測試。

### Step 3：寫 suggested_position_size 盤中 guardrail 的失敗測試

在 `backend/tests/test_strategy_generator.py` 加入：

```python
def test_suggested_position_size_intraday_is_conservative():
    """盤中時 suggested_position_size 應為保守文字，不出現積極建議"""
    from ai_stock_sentinel.analysis.strategy_generator import generate_action_plan

    result = generate_action_plan(
        strategy_type="short_term",
        conviction_level="medium",
        flow_label="institutional_accumulation",
        confidence_score=75.0,
        is_final=False,
        entry_zone="150-155",
        stop_loss="145",
        holding_period="1-2 週",
        rsi14=45.0,
        data_confidence=70.0,
    )
    pos_size = result.get("suggested_position_size", "")
    assert "盤中" in pos_size or "收盤確認" in pos_size, (
        f"盤中時應輸出保守提示，但得到：{pos_size}"
    )


def test_suggested_position_size_final_is_normal():
    """收盤後 is_final=True 時，suggested_position_size 不應出現『盤中』字樣"""
    from ai_stock_sentinel.analysis.strategy_generator import generate_action_plan

    result = generate_action_plan(
        strategy_type="short_term",
        conviction_level="medium",
        flow_label="institutional_accumulation",
        confidence_score=75.0,
        is_final=True,
        entry_zone="150-155",
        stop_loss="145",
        holding_period="1-2 週",
        rsi14=45.0,
        data_confidence=70.0,
    )
    pos_size = result.get("suggested_position_size", "")
    assert "盤中" not in pos_size
```

### Step 4：執行測試確認失敗

```bash
cd backend && python -m pytest tests/test_strategy_generator.py::test_suggested_position_size_intraday_is_conservative -v
```

預期：FAIL（目前無 is_final 判斷）

### Step 5：在 `generate_action_plan()` 加入盤中 guardrail

找到 `strategy_generator.py` 中 `generate_action_plan()` 的 `suggested_position_size` 生成邏輯，在最終 return 之前加入：

```python
# 盤中 guardrail：覆蓋 suggested_position_size
if not is_final:
    suggested_position_size = "盤中觀察，建議等待收盤確認後再評估部位"
```

確保這段在 `suggested_position_size` 計算完成之後、return 之前執行。

### Step 6：執行測試確認通過

```bash
cd backend && python -m pytest tests/test_strategy_generator.py -v -k "position_size"
```

預期：兩個新測試均 PASS

### Step 7：執行全部 strategy_generator 測試確認無回歸

```bash
cd backend && python -m pytest tests/test_strategy_generator.py -v
```

預期：全部 PASS

### Step 8：Commit

```bash
git add backend/src/ai_stock_sentinel/analysis/strategy_generator.py backend/tests/test_strategy_generator.py
git commit -m "feat: 盤中時 suggested_position_size 輸出保守提示 (P1)"
```

---

## Task 2：回測結果持久化 DB Schema

對應 spec：`docs/specs/p1-backtest-result-persistence-spec.md`

**Files:**
- Modify: `backend/src/ai_stock_sentinel/db/models.py`
- Create: `backend/alembic/versions/<timestamp>_add_backtest_tables.py`（由 alembic 生成）
- Modify: `backend/scripts/backtest_win_rate.py`
- Delete: `backend/backtest-results/`（整個目錄）

### Step 1：在 models.py 新增 BacktestRun 與 BacktestResult

在 `backend/src/ai_stock_sentinel/db/models.py` 的現有模型之後加入：

```python
class BacktestRun(Base):
    __tablename__ = "backtest_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    hold_days: Mapped[int] = mapped_column(Integer, nullable=False)
    days_lookback: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    draw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    results: Mapped[list["BacktestResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BacktestResult(Base):
    __tablename__ = "backtest_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_date: Mapped[date] = mapped_column(Date, nullable=False)
    p0_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    pN_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # win/loss/draw/skip
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    conviction_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    strategy_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    action_tag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    log_id: Mapped[int | None] = mapped_column(
        ForeignKey("daily_analysis_log.id"), nullable=True
    )

    run: Mapped["BacktestRun"] = relationship(back_populates="results")
```

確認所需 import（`Date`, `Decimal`, `ForeignKey`, `relationship`, `Text`）已在檔案頂部 import。

### Step 2：生成 Alembic migration

```bash
cd backend && alembic revision --autogenerate -m "add backtest run and result tables"
```

確認生成的 migration 檔包含 `backtest_run` 與 `backtest_result` 兩張表的 `upgrade()` 與 `downgrade()`。

### Step 3：執行 migration

```bash
cd backend && alembic upgrade head
```

預期：無報錯

### Step 4：驗證 downgrade 可逆

```bash
cd backend && alembic downgrade -1 && alembic upgrade head
```

預期：兩步均無報錯

### Step 5：在 backtest_win_rate.py 加入 DB 寫入函數

在腳本的適當位置（import 區之後、main 函數之前）加入：

```python
def _save_run_to_db(
    session,
    mode: str,
    hold_days: int,
    days_lookback: int,
    results: list[dict],
) -> None:
    """將回測執行結果寫入 DB"""
    from ai_stock_sentinel.config import STRATEGY_VERSION
    from ai_stock_sentinel.db.models import BacktestRun, BacktestResult
    from datetime import date, datetime

    wins = [r for r in results if r["outcome"] == "win"]
    losses = [r for r in results if r["outcome"] == "loss"]
    draws = [r for r in results if r["outcome"] == "draw"]
    skips = [r for r in results if r["outcome"] == "skip"]
    valid = [r for r in results if r["outcome"] != "skip"]
    win_rate = (len(wins) / len(valid) * 100) if valid else None

    run = BacktestRun(
        run_date=date.today(),
        mode=mode,
        hold_days=hold_days,
        days_lookback=days_lookback,
        strategy_version=STRATEGY_VERSION,
        total_samples=len(results),
        win_count=len(wins),
        loss_count=len(losses),
        draw_count=len(draws),
        skip_count=len(skips),
        win_rate=win_rate,
    )
    session.add(run)
    session.flush()  # 取得 run.id

    for r in results:
        session.add(BacktestResult(
            run_id=run.id,
            symbol=r["symbol"],
            signal_date=r["signal_date"],
            p0_price=r.get("p0_price"),
            pN_price=r.get("pN_price"),
            pct_change=r.get("pct_change"),
            outcome=r["outcome"],
            skip_reason=r.get("skip_reason"),
            signal_confidence=r.get("signal_confidence"),
            conviction_level=r.get("conviction_level"),
            strategy_type=r.get("strategy_type"),
            action_tag=r.get("action_tag"),
            log_id=r.get("log_id"),
        ))
    session.commit()
```

### Step 6：在 main 執行流程結尾呼叫 DB 寫入，移除 --output-json 寫檔邏輯

找到 `--output-json` 的 JSON 寫檔程式碼段，替換為：

```python
# 寫入 DB
try:
    with get_session() as session:
        _save_run_to_db(
            session=session,
            mode=args.mode,
            hold_days=args.hold_days,
            days_lookback=args.days,
            results=all_results,
        )
    print("[backtest] 結果已寫入 DB")
except Exception as e:
    print(f"[backtest] DB 寫入失敗：{e}", file=sys.stderr)
    sys.exit(1)
```

### Step 7：移除 backtest-results 目錄

```bash
rm -rf backend/backtest-results/
```

確認 `.gitignore` 是否有 `backtest-results/` 規則，若有一併移除。

### Step 8：手動驗證腳本執行

```bash
cd backend && python scripts/backtest_win_rate.py --mode new-position --days 30
```

預期：console 印出勝率摘要，且 `backtest_run` 有一筆新記錄。

用 psql 或 DB 工具查詢：
```sql
SELECT * FROM backtest_run ORDER BY created_at DESC LIMIT 1;
SELECT COUNT(*) FROM backtest_result WHERE run_id = <剛剛的 id>;
```

### Step 9：Commit

```bash
git add backend/src/ai_stock_sentinel/db/models.py backend/alembic/versions/ backend/scripts/backtest_win_rate.py
git commit -m "feat: 回測結果持久化至 DB，移除 backtest-results 目錄 (P1)"
```

---

## Task 3：前端策略卡四段式重構

對應 spec：`docs/specs/p1-analyze-strategy-card-spec.md`

**Files:**
- Modify: `frontend/src/pages/AnalyzePage.tsx`

### Step 1：確認 AnalyzeResponse.action_plan 型別已含所有欄位

打開 `frontend/src/pages/AnalyzePage.tsx`，確認 `action_plan` interface 中包含：

- `upgrade_triggers?: string[]`
- `downgrade_triggers?: string[]`
- `suggested_position_size?: string`

若缺少，先補齊型別定義。

### Step 2：找到策略卡渲染區塊

搜尋目前策略卡的 JSX 起點（通常有 `action_plan_tag` 或 `action_plan?.action` 的判斷）。記錄行號範圍。

### Step 3：將策略卡重構為四段式結構

以下為重構後的 JSX 結構範本（根據實際 className 系統調整）：

```tsx
{result?.action_plan && (
  <div className="rounded-xl border border-card-border bg-card-bg p-4 space-y-4">

    {/* 段落一：建議動作 */}
    <div className="flex items-start justify-between gap-2">
      <p className="text-sm font-medium text-card-text flex-1">
        {result.action_plan.action}
      </p>
      <div className="flex items-center gap-1.5 shrink-0">
        {!result.is_final && (
          <span className="rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-800">
            盤中版
          </span>
        )}
        {result.action_plan.conviction_level && (
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONVICTION_BADGE[result.action_plan.conviction_level].cls}`}>
            {CONVICTION_BADGE[result.action_plan.conviction_level].label}
          </span>
        )}
      </div>
    </div>

    {/* 段落二：主要理由 */}
    {result.action_plan.thesis_points && result.action_plan.thesis_points.length > 0 && (
      <div>
        <p className="text-xs font-semibold text-muted mb-1.5">主要理由</p>
        <ul className="space-y-1">
          {result.action_plan.thesis_points.map((point, i) => (
            <li key={i} className="flex gap-1.5 text-sm text-card-text">
              <span className="text-muted shrink-0">·</span>
              {point}
            </li>
          ))}
        </ul>
      </div>
    )}

    {/* 段落三：關鍵價位 */}
    <div className="rounded-lg bg-surface-secondary p-3 grid grid-cols-2 gap-2">
      <p className="text-xs font-semibold text-muted col-span-2 mb-0.5">關鍵價位</p>
      {result.action_plan.target_zone && (
        <div>
          <p className="text-xs text-muted">進場區間</p>
          <p className="text-sm font-medium text-card-text">{result.action_plan.target_zone}</p>
        </div>
      )}
      {result.action_plan.defense_line && (
        <div>
          <p className="text-xs text-muted">停損位</p>
          <p className="text-sm font-medium text-card-text">{result.action_plan.defense_line}</p>
        </div>
      )}
      {result.action_plan.momentum_expectation && (
        <div className="col-span-2">
          <p className="text-xs text-muted">動能預期</p>
          <p className="text-sm text-card-text">{result.action_plan.momentum_expectation}</p>
        </div>
      )}
      {result.action_plan.suggested_position_size && (
        <div className="col-span-2">
          <p className="text-xs text-muted">建議部位規模</p>
          <p className="text-sm text-card-text">{result.action_plan.suggested_position_size}</p>
        </div>
      )}
    </div>

    {/* 段落四：失效條件 */}
    {result.action_plan.invalidation_conditions && result.action_plan.invalidation_conditions.length > 0 && (
      <div>
        <p className="text-xs font-semibold text-muted mb-1.5">失效條件</p>
        <ul className="space-y-1">
          {result.action_plan.invalidation_conditions.map((cond, i) => (
            <li key={i} className="flex gap-1.5 text-sm text-card-text">
              <span className="text-rose-400 shrink-0">⚠</span>
              {cond}
            </li>
          ))}
        </ul>
      </div>
    )}

    {/* 可收合：條件變化 */}
    <TriggersSection
      upgradeTriggers={result.action_plan.upgrade_triggers}
      downgradeTriggers={result.action_plan.downgrade_triggers}
    />

    {/* 免責聲明（移至底部） */}
    {result.intraday_disclaimer && (
      <p className="text-xs text-muted border-t border-card-border pt-2">
        {result.intraday_disclaimer}
      </p>
    )}
  </div>
)}
```

### Step 4：實作 TriggersSection 可收合元件

在同一檔案（或抽成小元件），加入：

```tsx
function TriggersSection({
  upgradeTriggers,
  downgradeTriggers,
}: {
  upgradeTriggers?: string[];
  downgradeTriggers?: string[];
}) {
  const [open, setOpen] = React.useState(false);
  const hasUpgrade = upgradeTriggers && upgradeTriggers.length > 0;
  const hasDowngrade = downgradeTriggers && downgradeTriggers.length > 0;

  if (!hasUpgrade && !hasDowngrade) return null;

  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-muted hover:text-card-text transition-colors"
      >
        <span>{open ? "▲" : "▼"}</span>
        條件變化
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {hasUpgrade && (
            <div>
              <p className="text-xs font-semibold text-emerald-600 mb-1">升級觸發</p>
              <ul className="space-y-0.5">
                {upgradeTriggers!.map((t, i) => (
                  <li key={i} className="text-xs text-card-text flex gap-1.5">
                    <span className="text-emerald-500 shrink-0">↑</span>{t}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {hasDowngrade && (
            <div>
              <p className="text-xs font-semibold text-amber-600 mb-1">降級觸發</p>
              <ul className="space-y-0.5">
                {downgradeTriggers!.map((t, i) => (
                  <li key={i} className="text-xs text-card-text flex gap-1.5">
                    <span className="text-amber-500 shrink-0">↓</span>{t}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

### Step 5：目視驗收策略卡

```bash
cd frontend && npm run dev
```

開啟 `/analyze`，搜尋任一股票，確認：
- 四段式結構顯示正確
- `conviction_level` 徽章和「盤中版」標籤（若盤中）並列
- `suggested_position_size` 顯示在「關鍵價位」段落
- 「條件變化」區塊預設收合，點擊可展開
- 免責聲明在底部

### Step 6：確認 action_plan 為 null 時不報錯

```bash
# 在瀏覽器 console 確認無 JS error
```

或在開發環境暫時 mock `result.action_plan = null` 確認卡片消失、無 error。

### Step 7：Commit

```bash
git add frontend/src/pages/AnalyzePage.tsx
git commit -m "feat: Analyze 策略卡四段式重構，新增條件變化收合區塊 (P1)"
```

---

## Spec Review

本計劃對應三份需求規格，實作前請逐條確認無歧義。

### 對照 `docs/specs/p1-analyze-strategy-card-spec.md`

| Spec 項目 | 對應 Task | 確認點 |
|---|---|---|
| F1：四段式結構 | Task 3 Step 3 | 四段落均有視覺分隔（space-y-4 + 各段標題） |
| F2-1：suggested_position_size 有值時顯示 | Task 3 Step 3 | 「關鍵價位」段落含該欄位，有條件渲染 |
| F2-2：null 時不渲染 | Task 3 Step 3 | `suggested_position_size && (...)` 條件判斷 |
| F3-1：triggers 放可收合區塊 | Task 3 Step 4 | `TriggersSection` 元件 |
| F3-2：預設收合 | Task 3 Step 4 | `useState(false)` 預設 closed |
| F3-3：兩者皆空時不渲染 | Task 3 Step 4 | `if (!hasUpgrade && !hasDowngrade) return null` |
| F4-1：關鍵價位用底色卡片 | Task 3 Step 3 | `bg-surface-secondary` 區塊 |
| F4-2：失效條件用警示圖示 | Task 3 Step 3 | `text-rose-400 ⚠` 圖示 |
| NF3：免責聲明移至底部 | Task 3 Step 3 | 放在整個卡片最末 |
| AC5：action_plan null 不報錯 | Task 3 Step 6 | `result?.action_plan && (...)` 整體包裹 |

### 對照 `docs/specs/p1-intraday-vs-close-split-spec.md`

| Spec 項目 | 對應 Task | 確認點 |
|---|---|---|
| F1-1：is_final=False 時 conviction ≤ medium | Task 1 Step 1 | 確認現有邏輯，不覆寫 |
| F2-1：is_final=False 時 suggested_position_size 為保守文字 | Task 1 Step 5 | guardrail 在 return 前覆蓋 |
| F2-3：is_final=True 時輸出不變 | Task 1 Step 6 | test_suggested_position_size_final_is_normal |
| F3-1：前端顯示「盤中版」amber 標籤 | Task 3 Step 3 | `!result.is_final && <span>盤中版</span>` |
| F3-3：is_final=True 時不顯示標籤 | Task 3 Step 3 | 條件 `!result.is_final` |
| AC1：conviction_level guardrail 單元測試 | Task 1 Step 1-2 | 確認現有測試覆蓋 |
| AC2：is_final=False → suggested_position_size 含「盤中」 | Task 1 Step 3 | 失敗測試 |
| AC3：is_final=True → suggested_position_size 正常 | Task 1 Step 3 | 失敗測試 |

### 對照 `docs/specs/p1-backtest-result-persistence-spec.md`

| Spec 項目 | 對應 Task | 確認點 |
|---|---|---|
| F1-1：BacktestRun / BacktestResult 模型 | Task 2 Step 1 | 含所有 spec 定義欄位 |
| F1-2：migration upgrade/downgrade | Task 2 Step 2-4 | 兩方向均驗證 |
| F1-3：cascade delete | Task 2 Step 1 | `cascade="all, delete-orphan"` |
| F2-1：執行後寫入 BacktestRun | Task 2 Step 5-6 | `_save_run_to_db()` |
| F2-2：每個樣本寫入 BacktestResult | Task 2 Step 5 | 迴圈寫入 |
| F2-3：--output-json 移除 | Task 2 Step 6 | 替換寫檔邏輯 |
| F2-5：DB 失敗不靜默 | Task 2 Step 6 | `except Exception: print + sys.exit(1)` |
| F2-6：strategy_version 從 config 讀取 | Task 2 Step 5 | `from config import STRATEGY_VERSION` |
| F3-1：移除 backtest-results 目錄 | Task 2 Step 7 | `rm -rf` |
| AC3：執行後 backtest_run 有新記錄 | Task 2 Step 8 | SQL 查詢驗證 |
| AC4：result 筆數與 console 樣本數一致 | Task 2 Step 8 | SQL COUNT + console 對比 |
| AC6：strategy_version 與 config 一致 | Task 2 Step 8 | SQL 查詢確認 |
