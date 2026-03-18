# P0：信心分數校準 Implementation Plan

> 類型：Implementation Plan
> 建立日期：2026-03-18
> 對應 Roadmap：`docs/research/post-new-position-strategy-optimization-roadmap.md` §5.2
> 前置依賴：`docs/plans/2026-03-18-p0-backtest-new-position.md` 完成，且回測樣本數各分箱 >= 10

---

## 1. 背景

`confidence_scorer.py` 使用固定權重計算 `signal_confidence`（0–100）：

- `institutional_accumulation`：+7
- `distribution`：-10
- `retail_chasing`：-8
- `positive` sentiment：+5
- `negative` sentiment：-5
- 技術面 `bullish`：+5 / `bearish`：-5
- 三維共振 bonus：+3
- 利多出貨 penalty：-7
- `DATE_UNKNOWN` penalty：-3

目前這些權重來自設計時的主觀判斷，尚未經過回測資料驗證。

**核心問題：**

`signal_confidence` 高的訊號，實際勝率是否真的更高？如果不是，高分反而會傷害使用者信任感。

---

## 2. 前提確認（開始前檢查）

1. `backtest_win_rate.py --mode new-position` 的 Pearson 分析已可輸出
2. 回測樣本：`signal_confidence` 有值的 `analysis_is_final = TRUE` 記錄 >= 30 筆
3. 各 confidence 分桶（<60 / 60-70 / 70-80 / 80+）各有至少 5 筆有效樣本

若樣本不足，本計劃中的「權重調整」部分應暫緩，但「分析與診斷」部分仍可執行。

---

## 3. 目標

1. 驗證 `signal_confidence` 與新倉策略實際勝率是否正相關
2. 若相關性不足，診斷是哪個維度的權重設計出了問題
3. 必要時提出權重調整建議，並在人工審核後才修改 `confidence_scorer.py`
4. 建立半自動調權流程（分析 → 提案 → 審核 → 修改 → 回測驗證）

---

## 4. 範圍與非目標

**範圍內：**

- 分析回測結果中 confidence 分桶 vs 勝率的對應關係
- 分析各維度（sentiment / inst_flow / technical）的貢獻與勝率的相關性
- 若問題明確，提出具體的權重調整提案（以文件形式，不直接修改程式碼）
- 人工審核通過後，修改 `confidence_scorer.py` 並更新 `STRATEGY_VERSION`
- 修改後重新執行回測，確認分桶勝率有改善

**範圍外：**

- 自動化調權（機器學習等）
- 即時信心分數調整（生產環境不做自動調權）
- 對持股診斷的 `signal_confidence` 做校準（另立計劃）

---

## 5. 分析步驟

### Task 1：信心分桶 vs 勝率分析

使用現有回測腳本輸出的 confidence 分桶統計（`<60 / 60-70 / 70-80 / 80+`），與新倉策略 5日/10日勝率對照：

**預期健康狀態：**

| 分桶 | 預期勝率趨勢 |
|---|---|
| 80+ | 最高 |
| 70-80 | 次高 |
| 60-70 | 中等 |
| <60 | 最低 |

若勝率趨勢與分桶不成單調遞增，代表 `confidence_score` 校準問題。

**診斷矩陣：**

| 現象 | 可能原因 |
|---|---|
| 高分桶勝率低於低分桶 | 高分條件（如三維共振 bonus）不具備預測力 |
| 各分桶勝率無差異 | 整體 confidence 計算對新倉無區分力，需重新設計 |
| 某分桶樣本極少 | 分桶閾值不合理，或策略輸出分布集中 |

---

### Task 2：維度貢獻分析

從 `DailyAnalysisLog.indicators` 取出各訊號維度，分組比較勝率：

**分組方式：**

1. 按 `inst_flow` 分組（`institutional_accumulation` vs `distribution` vs `neutral`）
2. 按 `sentiment_label` 分組（`positive` vs `negative` vs `neutral`）
3. 按 `technical_signal` 分組（`bullish` vs `bearish` vs `sideways`）

**目標：** 哪個維度對新倉勝率的解釋力最強，哪個最弱。

**實作方式：**

在 `backtest_win_rate.py` 新增 `--breakdown-dimensions` 選項，從 `indicators` JSONB 中提取各維度標籤並分組統計。或以一次性分析腳本（`scripts/analyze_confidence_breakdown.py`）實作，不污染主腳本。

**建議採用獨立腳本**，原因：此分析是一次性校準工具，不需長期維護。

---

### Task 3：產出調權提案（若需要）

若 Task 1 / Task 2 發現問題，產出調權提案文件，格式如下：

```markdown
## 信心分數調權提案 YYYY-MM-DD

### 發現問題
- [描述具體的分桶勝率異常]

### 診斷
- [描述問題來自哪個維度]

### 提案變更
| 維度 | 現有值 | 建議值 | 理由 |
|---|---|---|---|
| `institutional_accumulation` | +7 | +5 | ... |

### 預期效果
- [描述預期改善方向]

### 風險
- [調整後可能影響持股診斷等其他用途]
```

提案存入 `docs/research/confidence-calibration-proposals/`。

---

### Task 4：人工審核 + 修改 + 驗證

1. 人工審核調權提案，確認邏輯合理
2. 修改 `confidence_scorer.py` 對應常數
3. 同步更新 `config.py` 的 `STRATEGY_VERSION`（minor version bump，如 `1.0.0` → `1.1.0`）
4. 重新執行回測：

```bash
python scripts/backtest_win_rate.py \
  --mode new-position \
  --days 90 \
  --require-final-raw-data \
  --output-json docs/research/backtest-results/new-position-post-calibration-$(date +%Y%m%d).json
```

5. 對比 baseline 與 post-calibration 的分桶勝率，確認有改善

---

### Task 5：半自動調權流程規範（文件化）

在 `confidence_scorer.py` 頂部 docstring 或 `docs/development-execution-playbook.md` 補充：

```
## 信心分數調權流程

1. 執行回測：python scripts/backtest_win_rate.py --mode new-position --output-json ...
2. 若分桶勝率不單調遞增，執行維度分析腳本
3. 產出調權提案文件至 docs/research/confidence-calibration-proposals/
4. 人工審核提案
5. 修改 confidence_scorer.py 常數
6. 更新 STRATEGY_VERSION（minor bump）
7. 重新執行回測確認改善
8. 若無改善，回滾並重新分析
```

---

## 6. 受影響模組

| 模組 | 變更類型 |
|---|---|
| `scripts/analyze_confidence_breakdown.py` | 新建（一次性分析腳本） |
| `analysis/confidence_scorer.py` | 權重常數調整（人工審核後） |
| `config.py` | `STRATEGY_VERSION` minor bump |
| `docs/research/confidence-calibration-proposals/` | 新建目錄，存放調權提案 |
| `docs/research/backtest-results/` | 存放 post-calibration 回測結果 |
| `docs/development-execution-playbook.md` | 補充調權流程說明 |

---

## 7. 測試計劃

| 測試 | 方法 |
|---|---|
| 維度分析腳本可執行 | `python scripts/analyze_confidence_breakdown.py --days 90` 不報錯 |
| 調整後回測可執行 | 修改 `confidence_scorer.py` 後回測腳本正常輸出 |
| `STRATEGY_VERSION` 已更新 | 新分析記錄的 `strategy_version` 為新版本號 |
| 分桶勝率改善 | post-calibration 回測的高分桶勝率 >= baseline |

---

## 8. 依賴與風險

| 項目 | 說明 |
|---|---|
| 樣本數不足 | 初期可能各分桶樣本數偏低，校準結論不可靠；應等樣本充足後再做 Task 3-4 |
| 權重調整影響持股診斷 | `confidence_scorer.py` 同時用於持股診斷（`/analyze/position`）；調整前需確認不影響持股端的邏輯語意 |
| 回測期間資料品質 | 若資料源在某段期間有已知問題，回測樣本需排除該期間 |
| 過度校準風險 | 不應根據少量樣本做多次調整（overfitting）；每次調整應至少間隔一個月的新樣本 |

---

## 9. 驗收定義

1. 信心分桶 vs 勝率分析報告已產出（即使結論是「暫無問題」）
2. 維度貢獻分析腳本可執行
3. 若有調整：`confidence_scorer.py` 已修改，`STRATEGY_VERSION` 已更新，post-calibration 回測結果已存入 `docs/research/backtest-results/`
4. 調權流程已文件化於 `development-execution-playbook.md`

---

## 10. Spec Review

對應需求規格：`docs/p0-confidence-calibration-spec.md`

實作前請確認 spec 中以下項目無歧義：

- 第 1 節（背景問題中的權重表）：確認與 `confidence_scorer.py` 現有常數一致，若已有調整需同步更新 spec
- F3-3：調權提案「未經人工審核前不得修改程式碼」的審核定義是否清楚（誰審、怎麼標記）
- F4-2：`STRATEGY_VERSION` minor bump 的觸發條件（§F4-2 僅指本次調整，與 P0 前置的 `1.0.0` 初始值銜接）
- 第 6 節（診斷矩陣）：「需要調權」與「不需要調權」的判斷邊界是否認同

驗收時對照 spec AC1 ~ AC7 逐條確認。
