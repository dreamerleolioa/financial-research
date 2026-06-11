# Investment Discipline Execution Commands

| Metadata | Value |
| --- | --- |
| Status | execution reference |
| Date | 2026-06-11 |
| Plan | `docs/plans/2026-06-11-investment-discipline-execution-plan.md` |
| Roadmap | `docs/plans/2026-06-11-investment-discipline-validation-roadmap.md` |
| Prompt file | `prompt-investment-discipline-execution.md` |

## 1. How To Use

Use this file as the operational command sheet for the investment discipline plan.

Rules:

1. Start with the readiness command.
2. Execute one phase per Codex thread or implementation session.
3. Prefer the copy-paste prompt in `prompt-investment-discipline-execution.md` for the matching phase.
4. Do not run the next phase until the current phase has a clean completion report.
5. For Phase 1, do not change live scoring. The output is a validation report only.

## 2. Readiness Command

Paste this before starting implementation if the worktree state or Daily Radar v2 checkpoint is unclear:

```text
請先做 Investment Discipline Execution Plan 的 Phase 0: Baseline Readiness Check。

請閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-11-investment-discipline-validation-roadmap.md
- docs/specs/daily-stock-radar-spec.md
- docs/plans/2026-06-10-daily-radar-v2-optimization-requirements.md
- docs/plans/2026-06-11-daily-radar-v2-phase-1d-backtest-calibration.md
- backend/src/ai_stock_sentinel/daily_radar/scoring.py
- backend/src/ai_stock_sentinel/daily_radar/calibration.py
- backend/src/ai_stock_sentinel/daily_radar/repository.py
- backend/src/ai_stock_sentinel/shared_context.py

請只回報：
1. Daily Radar candidate trace 是否足夠支援 forward validation。
2. shared background context 是否已和 daily trigger signal 分離。
3. 是否有未完成 worktree 變更會干擾 Phase 1。
4. Phase 1 的 go/no-go 結論。
5. 若 no-go，列出最小 blocker fix。

不要實作 Phase 1。
```

## 3. Recommended First Implementation Command

The first implementation slice should be Phase 1 / roadmap Phase B: Forward Outcome Validation.

Paste this command into a fresh implementation session:

```text
請執行 Investment Discipline Execution Plan 的 Phase 1: Forward Outcome Validation。

請使用 `prompt-investment-discipline-execution.md` 裡的「Phase 1 Prompt: Forward Outcome Validation」作為詳細執行指令；若該 prompt 和 execution plan 有衝突，以 execution plan 的「不改 live scoring、只產出 deterministic offline validation report」為優先。

完成標準：
1. 新增 deterministic forward validation module/script。
2. 支援 fixture mode 與 db mode；資料不足時列 skip reasons。
3. 支援 5、10、20 trading-day windows。
4. report 至少包含 metadata、sample_summary、bucket_outcomes、rule_outcomes、risk_label_outcomes、market_regime_outcomes、score_decile_outcomes、ablation_candidates、skip_reasons、version_manifest。
5. 不改 live scoring、ranking、bucket 判定、risk penalty 或 scoring/rule version。
6. 新增/更新測試，證明 outcome math、missing data、grouping、report determinism。

完成後停止，回報 changed files、驗證命令、fixture report 摘要、db mode 限制、下一個建議 phase。
```

## 4. Phase Command Map

| Execution phase | Roadmap phase | Copy-paste prompt section | Default status |
| --- | --- | --- | --- |
| Phase 0: Baseline Readiness Check | Start criteria | This file section 2 | Run before implementation if state is unclear |
| Phase 1: Forward Outcome Validation | Roadmap Phase B | `prompt-investment-discipline-execution.md` Phase 1 | Recommended first implementation |
| Phase 2: Entry Strategy Context Audit And Gap Closure | Entry record requirement | This file section 5 or prompt Phase 2 | Run after Phase 1 or in parallel only if carefully separated |
| Phase 3: Rule Pruning And Score Governance | Roadmap Phase C | `prompt-investment-discipline-execution.md` Phase 3 | Run after Phase 1 report exists |
| Phase 4: Risk Language Alignment | Roadmap Phase A | `prompt-investment-discipline-execution.md` Phase 4 | Can run earlier if public copy risk is urgent |
| Phase 5: Portfolio Risk Layer | Roadmap Phase D | `prompt-investment-discipline-execution.md` Phase 5 | Run after risk-language vocabulary is stable |
| Phase 6: Release Gate And Monitoring | Roadmap Phase E | `prompt-investment-discipline-execution.md` Phase 6 | Run after at least Phases 1, 3, and 5 |

## 5. Entry Strategy Context Audit Command

Use this phase to verify the existing entry-context implementation and close only the gaps between "the trade lost money" and "the original strategy was wrong."

```text
請執行 Investment Discipline Execution Plan 的 Phase 2: Entry Strategy Context Audit And Gap Closure。

請先閱讀：
- docs/plans/2026-06-11-investment-discipline-execution-plan.md
- docs/plans/2026-06-09-entry-record-optimization-requirement.md
- docs/plans/2026-06-08-position-lifecycle-entry-exit-analysis.md
- backend/src/ai_stock_sentinel/portfolio/entry_record_contract.py
- backend/src/ai_stock_sentinel/portfolio/router.py
- backend/src/ai_stock_sentinel/analysis/position_lifecycle.py
- backend/src/ai_stock_sentinel/db/models.py
- frontend/src/pages/PortfolioPage.tsx
- frontend/src/lib/portfolioTypes.ts
- backend/tests/test_entry_record_contract.py
- backend/tests/test_portfolio_router.py
- backend/tests/test_position_lifecycle_analysis.py

本階段目標：
1. 先 audit 既有實作，不要重複實作已存在的 entry_record / PositionEvent / PositionLifecyclePlan / frontend flows。
2. 確認新建倉已捕捉 entry reason、planned holding period、default stop rule、add-entry condition。
3. 確認 optional note 只能作 secondary evidence，不可取代固定欄位。
4. 確認 provenance：user_recorded_at_event_time、user_backfilled、synthetic_from_portfolio_row、manual_record_correction、not_recorded。
5. 確認 lifecycle review 使用固定欄位判斷 plan adherence；缺資料時保留 decision_context: insufficient。
6. 只有發現缺口時才改程式；沒有缺口時回報 no-op 和驗證證據。

限制：
1. 不可用 PnL、後續走勢或 LLM 推論原始意圖。
2. 不可因缺少 fixed fields 阻塞 existing lifecycle review。
3. 不可把 backfilled plan 當作 entry-time plan。

完成後停止，回報 audit 結論、changed files、schema/API/UI 變更、provenance 策略、驗證命令、仍需 deferred 的 add-entry/backfill 項目。
```

## 6. Verification Commands

Docs-only verification:

```bash
rg -n "Investment Discipline Execution Plan|Forward Outcome Validation|Entry Strategy Context|Audit And Gap Closure" docs/plans prompt-investment-discipline-execution.md
```

Existing Daily Radar calibration command:

```bash
cd backend
uv run python scripts/daily_radar_calibration.py \
  --source fixture \
  --run-date 2026-05-29
```

Expected Phase 1 command after implementation:

```bash
cd backend
uv run python scripts/daily_radar_forward_validation.py \
  --market TW \
  --start-date 2026-06-01 \
  --end-date 2026-06-30 \
  --windows 5,10,20 \
  --output /tmp/daily-radar-forward-validation.json
```

Rule governance checks after Phase 3:

```bash
cd backend
uv run pytest -q tests/test_daily_radar_rule_governance.py
```

Portfolio risk checks after Phase 5:

```bash
cd backend
uv run pytest -q tests/test_portfolio_router.py
```

Frontend build after UI/copy changes:

```bash
cd frontend
pnpm build
```

Copy scan after Phase 4:

```bash
rg -n "建議買|建議賣|買進|賣出|加碼|減碼|出場|必買|目標價|勝率" frontend/src backend/src docs/specs docs/plans
```

The copy scan requires an allowlist after Phase 4 because specs and compatibility-field docs may intentionally mention deprecated command-language terms.

## 7. Completion Report Template

Every phase should end with:

```text
Phase completed:
- Scope:
- Changed files:
- Behavioral changes:
- Backward compatibility:
- Verification:
- Manual QA:
- Data or sample limitations:
- Residual risks:
- Next recommended phase:
```
