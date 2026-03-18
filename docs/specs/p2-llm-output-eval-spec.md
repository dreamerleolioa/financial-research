# P2：LLM 輸出評測 需求規格

> 類型：需求規格（Spec）
> 建立日期：2026-03-18
> 對應計劃：`docs/plans/2026-03-19-p2-p3-implementations.md`
> 對應 Roadmap：§5.6
> 前置依賴：P1 全部完成（策略卡升級、盤中分流）

---

## 1. 背景問題

策略判斷本身是 rule-based，但使用者最終看到的「分析敘事」由 LLM 撰寫（`langchain_analyzer.py`）。目前 LLM 輸出有以下已知風險：

1. **跨維度混寫**：`tech_insight` 出現法人資料、`inst_insight` 引用新聞，違反 system prompt 的隔離規範
2. **與 rule-based 結論衝突**：`final_verdict` 文字偏樂觀但 `conviction_level = low`，或文字帶出場語氣但策略是新倉建議
3. **過度武斷或肯定**：使用「必然」、「確定」、「100%」等不應出現於分析文字的措辭
4. **造假或誇大**：引用輸入資料中未出現的數字或來源

沒有評測機制，這些問題只能靠人工偶發察覺，無法系統性監控。

---

## 2. 目標

| # | 目標 |
|---|---|
| G1 | 建立固定 regression eval 案例集，可重複執行驗證 LLM 輸出品質 |
| G2 | 評測涵蓋三類問題：維度越界、結論衝突、過度武斷 |
| G3 | 評測腳本輸出結構化報告，標注通過/警告/失敗各條 |
| G4 | 為 system prompt 建立版本追蹤（prompt hash），讓 prompt 修改可被偵測 |

---

## 3. 範圍

### 範圍內

- `scripts/eval_llm_output.py`：評測腳本（讀取固定案例集，輸出報告）
- `tests/fixtures/llm_eval_cases.json`：固定 eval 案例集（至少 5 筆）
- `langchain_analyzer.py`：system prompt 版本 hash 計算（不修改 prompt 本身）
- 評測報告儲存至 `docs/research/llm-eval-results/`

### 範圍外

- 自動修改 system prompt（需人工審核）
- 自動化 CI 執行評測（P3+，成本考量）
- 對持股診斷 prompt（`_POSITION_SYSTEM_PROMPT`）的評測（另立計劃）
- LLM 模型替換或微調

---

## 4. 功能需求

### F1：eval 案例集格式

| 編號 | 需求 |
|---|---|
| F1-1 | 案例集存放於 `backend/tests/fixtures/llm_eval_cases.json` |
| F1-2 | 每筆案例包含：`id`、`description`、`mock_input`（模擬傳入 LLM 的 context）、`expected_checks`（期望通過的檢查列表） |
| F1-3 | 初期至少涵蓋以下 5 類案例：正常輸出、tech_insight 維度越界、final_verdict 與 conviction 衝突、過度武斷措辭、造假數字 |
| F1-4 | `mock_input` 使用真實格式的假資料（不需要呼叫實際 API），可 offline 執行 |

### F2：評測腳本

| 編號 | 需求 |
|---|---|
| F2-1 | `scripts/eval_llm_output.py` 接受 `--cases` 參數（預設讀 `tests/fixtures/llm_eval_cases.json`） |
| F2-2 | 對每筆案例，呼叫實際 LLM API 取得輸出，再執行 `expected_checks` |
| F2-3 | 支援 `--dry-run`：只印出案例資訊，不呼叫 LLM（供本機快速驗證腳本結構） |
| F2-4 | 每個 check 結果為 `pass`、`warn`、`fail` 三態 |
| F2-5 | 支援 `--output-json` 輸出完整結果至 JSON 檔 |

### F3：評測規則（`expected_checks` 種類）

| Check 名稱 | 通過條件 | 失敗條件 |
|---|---|---|
| `no_cross_dimension` | `tech_insight` 不含「法人」「買超」「賣超」字樣；`inst_insight` 不含 RSI 數字或「均線」字樣 | 反之則 fail |
| `verdict_conviction_align` | `final_verdict` 的語氣與 `conviction_level` 大致一致（low → 保守語氣；high → 正向語氣） | 嚴重衝突則 fail |
| `no_overconfident_language` | 不含「必然」「確定」「100%」「一定會」等字串 | 含則 warn |
| `no_fabricated_source` | 不含「根據 XX 研究機構」「XX 分析師表示」等輸入中未出現的來源格式 | 含則 fail |
| `json_valid` | 輸出可被 `json.loads()` 解析，且含 `final_verdict`、`tech_insight`、`inst_insight` key | 解析失敗或缺 key 則 fail |

### F4：system prompt 版本 hash

| 編號 | 需求 |
|---|---|
| F4-1 | `langchain_analyzer.py` 在初始化時計算 `_SYSTEM_PROMPT` 的 MD5 hash，存為 `PROMPT_HASH` 常數 |
| F4-2 | 每次分析結果的 `analysis_detail` 中加入 `prompt_hash` 欄位（只讀，不寫入 DB） |
| F4-3 | 評測報告記錄執行時的 `prompt_hash`，讓不同時間的評測結果可比較 |

### F5：評測報告格式

| 編號 | 需求 |
|---|---|
| F5-1 | 結果存入 `docs/research/llm-eval-results/YYYYMMDD-<prompt_hash_short>.json` |
| F5-2 | 頂層欄位：`run_date`、`prompt_hash`、`total`、`pass_count`、`warn_count`、`fail_count`、`cases` |
| F5-3 | 每筆 `cases` 含：`id`、`description`、`checks`（每條 check 的結果與說明） |
| F5-4 | `fail_count > 0` 時腳本以 exit code 1 結束（讓 CI 可偵測） |

---

## 5. 非功能需求

| 編號 | 需求 |
|---|---|
| NF1 | 評測腳本只讀取資料，不寫入 DB，不影響任何 API 行為 |
| NF2 | `--dry-run` 模式完全 offline，不需 LLM API key |
| NF3 | 評測結果 JSON 不包含使用者個人資料 |

---

## 6. 驗收條件（DoD）

| # | 驗收條件 | 驗證方式 |
|---|---|---|
| AC1 | `python scripts/eval_llm_output.py --dry-run` 可執行，印出 5 筆案例 | CLI 執行 |
| AC2 | `python scripts/eval_llm_output.py` 完整執行，輸出 pass/warn/fail 統計 | CLI 執行 |
| AC3 | 手動注入違規 LLM 輸出（含「法人」字樣至 tech_insight），對應 check 輸出 `fail` | mock 測試 |
| AC4 | `--output-json` 輸出包含 F5 定義的所有欄位 | 驗證 JSON |
| AC5 | `langchain_analyzer.py` 可計算並輸出 `PROMPT_HASH` | import 確認 |
| AC6 | `docs/research/llm-eval-results/` 有第一次評測結果 JSON | 確認檔案 |

---

## 7. 依賴

| 依賴項目 | 說明 |
|---|---|
| LLM API key 可用 | 執行完整評測需 Anthropic API key（`--dry-run` 不需要） |
| `langchain_analyzer.py` 的 `_SYSTEM_PROMPT` 格式穩定 | prompt hash 計算的基礎 |

---

## 8. 開放問題

| 問題 | 狀態 |
|---|---|
| `verdict_conviction_align` 的「語氣一致」如何量化？ | 決定：第一版用關鍵字比對（low conviction → 不應出現「強烈建議」「積極布局」；high → 不應出現「謹慎觀望」「不建議」） |
| 評測腳本是否應定期自動執行？ | 決定：P2 階段為手動執行，CI 自動化留 P3+ |
