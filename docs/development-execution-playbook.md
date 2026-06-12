# AI Stock Sentinel 開發執行手冊（Execution Playbook）

> 版本：v3.0
> 更新日期：2026-06-12
> 定位：本文件是開發節奏、驗證 Gate、文件同步與 release 檢查的操作手冊。功能與 API 的正式事實不放在本文件，請回寫到 `docs/specs/` 對應規格。

---

## 1) Canonical Sources

目前長期架構與功能事實集中在 `docs/specs/`。短期討論、agent 對話或臨時計劃不得取代下列文件。

| 類型 | Canonical 文件 | 更新時機 |
| ---- | -------------- | -------- |
| 架構與模組邊界 | `docs/specs/ai-stock-sentinel-architecture-spec.md` | 後端模組、資料流、DB 表、workflow、shared context 邊界改變時 |
| API contract | `docs/specs/backend-api-technical-spec.md` | request/response schema、endpoint、錯誤碼或 internal API contract 改變時 |
| Daily Radar | `docs/specs/daily-stock-radar-spec.md` | universe、scoring、request budget、shared context、forward validation、rule governance 改變時 |
| 持股診斷與 lifecycle | `docs/specs/ai-stock-sentinel-position-diagnosis-spec.md` | `/analyze/position`、portfolio review、lifecycle review 行為改變時 |
| 自動化 review 與歷史資料 | `docs/specs/ai-stock-sentinel-automation-review-spec.md` | 每日紀錄、cache、history loader、analysis log 行為改變時 |
| Roadmap / release 決策 | `docs/specs/ai-stock-sentinel-execution-roadmap-spec.md` | 階段性需求、release gate、否決決策改變時 |
| 專案入口 | `README.md` | 啟動方式、部署、環境變數、主要功能入口改變時 |
| 後端自學導覽 | `docs/backend-self-study-guide.md` | 後端技術棧、模組讀法、需求實作對照或學習路線改變時 |

維護原則：

- 不再把 `docs/plans/` 視為長期架構事實來源。
- API 欄位的唯一正式 contract 是 `backend-api-technical-spec.md`；其他文件描述語意與邊界，避免複製完整 schema 後漂移。
- Daily Radar 是 deterministic backend workflow，不得在文件中描述成 LLM 選股。
- `shared_background_contexts` 是 evidence/cache，不是 action、ranking、verdict 或 classification 的覆寫來源。

---

## 2) 目前系統基線

| 表面 | 入口 | 開發時必守邊界 |
| ---- | ---- | -------------- |
| 新倉分析 | `/analyze`, frontend `/analyze` | 用於研究 setup 與觀察條件；Python rule-based code 產生技術指標、風險語言與 trace；LLM 不估算數值、不覆寫 deterministic 欄位 |
| 持股診斷 | `/analyze/position`, frontend `/portfolio` | 用於既有部位風險、續抱、減碼、出場檢查；不得回流成新倉建議 |
| 持股紀律與復盤 | `/portfolio/*` | 以 `position_group_id` 串起持股、加碼、結案、事件 ledger、entry context、lifecycle plan、review |
| Daily Radar | `/internal/daily-radar/*`, `GET /daily-radar/*`, frontend `/daily-radar` | 收盤後產生隔日觀察清單；`observation_score` 用於排序、校準與 trace，不是勝率或交易建議 |
| Shared Context | `shared_background_contexts`, `shared_context.py` | 只作 evidence、caveat、data quality trace；missing/stale/not-applicable 不阻斷主要 workflow |

---

## 3) 開發原則

- 先確認現有 backend/frontend seam，再補功能；不要重做已存在的 entry context、lifecycle review 或 shared context flow。
- 每個變更都要定義 DoD、驗證方式與文件落點。
- 程式、測試、文件同一輪完成；不能只改行為不改 contract。
- 使用 production-like cloud path 設計正式 Daily Radar validation/report flow；本機 fixture 只作單元測試與快速驗證。
- 使用中文使用者文案時，不直接暴露 backend English enum；在 frontend display mapping 或 API readable label 層處理。
- 交通與資料來源安全是 hard boundary；不可用不可驗證 TLS 的資料流通過正式 pipeline。

---

## 4) 任務執行流程

### 4.1 開始前

1. 檢查工作樹狀態，保留使用者既有變更。
   ```bash
   git status --short
   ```
2. 找 canonical 文件與實作 seam。
   ```bash
   rg -n "keyword|endpoint|model|context_type" backend frontend docs/specs
   ```
3. 判斷是否需要同步 API spec、Daily Radar spec、position spec 或 README。

### 4.2 實作中

- 後端優先沿用既有 service/repository/provider/router pattern。
- 前端優先沿用既有 page、lib、type、formatter 與中文 label mapping。
- DB schema 變更需同時更新 SQLAlchemy model、Alembic migration、API contract 與測試。
- Shared context 變更需同時檢查 `applicable_consumers`、freshness、replay key、point-in-time 行為與 caveat。

### 4.3 完成前

1. 跑與變更範圍相符的測試。
2. 跑文件與格式基本檢查。
3. 更新 canonical docs。
4. 在交付摘要列出：改了什麼、驗證了什麼、哪些測試未跑。

---

## 5) 驗證矩陣

### 5.1 後端

一般後端變更：

```bash
cd backend
uv run pytest -q
```

聚焦測試範例：

```bash
cd backend
uv run pytest -q tests/test_daily_radar_service.py tests/test_daily_radar_api.py
uv run pytest -q tests/test_portfolio_router.py tests/test_portfolio_history.py
uv run pytest -q tests/test_position_lifecycle_analysis.py tests/test_trade_review.py
```

### 5.2 前端

```bash
cd frontend
pnpm build
pnpm lint
```

涉及 UI 行為或 responsive layout 時，還需啟動本機 dev server 並用瀏覽器檢查主要頁面。

```bash
cd frontend
pnpm dev
```

### 5.3 Release Gate

投資紀律、Daily Radar、portfolio lifecycle 或風險語言相關變更，至少跑 release gate 覆蓋面：

```bash
cd backend
uv run pytest -q \
  tests/test_daily_radar_rule_governance.py \
  tests/test_daily_radar_forward_validation.py \
  tests/test_risk_language_copy_guard.py \
  tests/test_portfolio_risk_summary.py \
  tests/test_portfolio_router.py \
  tests/test_portfolio_history.py \
  tests/test_investment_discipline_release_gate.py \
  tests/test_compatibility_deprecation_audit.py
```

並跑前端 build：

```bash
cd frontend
pnpm build
```

### 5.4 文件檢查

```bash
git diff --check -- README.md docs/specs docs/development-execution-playbook.md
rg -n 'requirements[.]txt|Rende[r]|Python 3[.]10|Node[.]js 20|docs/plans/202[6]|monthly-rule[-]review' README.md docs/specs
```

`docs/plans/` 字串本身可在 specs 維護規則中出現，但不得再指向已刪除的舊日期 plan 作為正式依據。

---

## 6) Gate 機制

| Gate | 條件 | 必要證據 |
| ---- | ---- | -------- |
| Contract Gate | API 欄位、錯誤碼、DB schema、frontend type 已同步 | backend API tests、frontend type/build、`backend-api-technical-spec.md` |
| Determinism Gate | Daily Radar ranking、score、bucket、risk label 不由 LLM 覆寫 | scoring/service tests、fixture/replay tests |
| Shared Context Gate | shared context 只作 evidence/caveat/data quality，不改 action/ranking/verdict/classification | shared context tests、consumer tests、spec 更新 |
| Portfolio Discipline Gate | entry record、event ledger、lifecycle plan/review 能 point-in-time 回放 | portfolio/lifecycle tests、position spec 更新 |
| Copy Guard Gate | 使用者文案採研究/風險語言，不用命令式買賣語言 | risk language copy tests、frontend build |
| Release Gate | backend release gate tests + frontend build 通過 | GitHub Actions 或本機等價命令輸出 |

未過 Gate 不進入部署或 PR merge。

---

## 7) Daily Radar Validation 與 Rule Governance

Daily Radar 的正式驗證與 rule review 必須使用 production DB / cloud internal API path，不以本機 fixture 報告作為正式月報。

### 7.1 Forward Validation

內部 API：

```text
POST /internal/daily-radar/forward-validation/run
```

用途：

- 找出 matured candidates。
- 從 production `stock_raw_data` 讀取候選與 benchmark price series。
- 寫入 `daily_radar_forward_validation_results`。
- 產生可回放 report JSON。

本機測試：

```bash
cd backend
uv run pytest -q tests/test_daily_radar_forward_validation.py
```

### 7.2 Monthly Rule Review

內部 API：

```text
POST /internal/daily-radar/rule-review/monthly
```

用途：

- 讀取 production validation results。
- 產生 `report_json` 與 `report_markdown`。
- 由 `.github/workflows/daily-radar-rule-review.yml` 上傳 artifact。

本機測試：

```bash
cd backend
uv run pytest -q tests/test_daily_radar_rule_governance.py
```

### 7.3 調整規則前的限制

- 樣本數低於 `DEFAULT_MIN_SAMPLE_COUNT` 時，不可自動提出強結論。
- 調整 scoring/rule 需要保留前後版本、validation evidence 與 monthly review rationale。
- 調整不得改變 historical replay 的 point-in-time 語意。

---

## 8) 策略版本與快取失效 SOP

### 8.1 觸發條件

| 變更類型 | 版次 | 範例 |
| -------- | ---- | ---- |
| docstring、log、非邏輯重構 | PATCH | `1.0.0 -> 1.0.1` |
| confidence 常數、risk language 模板、copy allowlist、輕量 rule threshold | MINOR | `1.0.0 -> 1.1.0` |
| scoring 核心邏輯、strategy/risk classification、Daily Radar bucket/ranking 規則 | MAJOR | `1.0.0 -> 2.0.0` |

LLM prompt 修改不屬於 strategy version；需用 prompt hash 或對應 prompt/version trace 追蹤。

### 8.2 操作步驟

1. 判斷版次。
2. 修改 `backend/src/ai_stock_sentinel/config.py` 的 `STRATEGY_VERSION`。
3. 跑相關後端測試與 release gate。
4. 確認 `StockAnalysisCache` 版本失效行為仍正確。
5. 若影響 Daily Radar，跑 forward validation / rule governance 相關測試。
6. 更新 `backend-api-technical-spec.md`、`daily-stock-radar-spec.md` 或 roadmap 決策。

---

## 9) Checkpoint 模板

- 日期：
- 本輪目標：
- 變更範圍：
- Canonical docs 更新：
- 後端驗證：
- 前端驗證：
- Release gate / skipped reason：
- DB / migration 影響：
- Shared context 影響：
- 風險與後續：

---

## 10) 文件維護規範

- 完成 API 變更時，同步 `backend-api-technical-spec.md`。
- 完成 Daily Radar scoring、validation、rule review 或 shared context 變更時，同步 `daily-stock-radar-spec.md`。
- 完成 portfolio、entry record、position event、trade review 或 lifecycle review 變更時，同步 `ai-stock-sentinel-position-diagnosis-spec.md`。
- 完成架構邊界、資料流、DB 表、workflow 變更時，同步 `ai-stock-sentinel-architecture-spec.md`。
- 完成啟動方式、環境變數、部署或主要入口變更時，同步 `README.md`。
- 文件不能只描述期望狀態；若功能尚未落地，必須標為 future / proposed，並寫清楚不影響目前 production path。
