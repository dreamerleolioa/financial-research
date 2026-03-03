# AI Stock Sentinel 開發執行手冊（Execution Playbook）

> 版本：v1.0  
> 更新日期：2026-03-03

## 1) 現況基線

- Phase 1（MVP Backend）：85%
- Phase 2（LangGraph 回圈）：0%
- Phase 3（分析能力強化）：20%
- Phase 4（前端儀表板）：35%

### 基線解讀
- 後端核心流程可跑，但缺 API 契約化與回圈補抓。
- 前端已有儀表板骨架，但目前仍以靜態資料為主。
- 建議優先序：API 契約 → LangGraph 回圈 → RSS 資料源 → 分析深化 → 前端真資料串接。

---

## 2) 開發原則與週節奏

### 開發原則
- 每週只追一個主目標，避免同時多線半成品。
- 每項任務需定義 DoD（可驗收條件）與測試證據。
- 每項功能完成後，當日補上對應測試（單元或整合）再視為 Done。
- 每週五必須可 Demo（至少一條可操作流程）。
- 文件與程式同步更新，不可脫節。

### 週節奏（固定）
- 週一：規劃（目標、風險、依賴、決策）
- 週二～週四：開發與整合
- 週五：Checkpoint（Demo + Gate 判定 + 文件更新）

---

## 3) 六週 Roadmap（每週目標 / 交付 / Checkpoint）

## Week 1：API 契約鎖定
- 目標：固定分析 API request/response 與錯誤碼。
- 交付：AnalyzeResponse v1、OpenAPI、合約測試。
- Checkpoint：`/health`、`/analyze` 可用，CLI/API 輸出核心欄位一致。

## Week 2：LangGraph 最小回圈
- 目標：建立 `crawl -> clean -> analyze -> judge -> loop`。
- 交付：state model、judge 節點、max retries、loop guard。
- Checkpoint：可重現「資料不足→補抓→再分析」。

## Week 3：RSS 新聞自動化
- 目標：降低手動貼新聞依賴。
- 交付：Google News RSS connector、metadata 標準化、去重規則。
- Checkpoint：symbol 可自動抓回至少 N 篇新聞，來源可追溯。

## Week 4：分析深化（可解釋）
- 目標：輸出 facts 與情緒詞分離，加入可驗證指標。
- 交付：`facts`、`emotional_terms`、`fact_only_summary`、`confidence_score`。
- Checkpoint：至少 2 個可驗證指標（含來源與計算依據）。

## Week 5：前端真資料串接
- 目標：儀表板改為 API 資料驅動。
- 交付：查詢流程、載入/錯誤/空態、來源時間戳。
- Checkpoint：輸入 symbol 後可完整看到真實分析結果與路徑事件。

## Week 6：穩定化與驗收
- 目標：完成發版前 Gate 與交接。
- 交付：回歸測試報告、runbook、風險與回滾策略。
- Checkpoint：關鍵流程通過，文件與程式一致。

---

## 4) Gate 機制（未過不進下一階段）

- G1（契約 Gate）：API 契約、錯誤碼、schema 定稿。
- G2（回圈 Gate）：LangGraph 補抓回圈與重試上限可運行。
- G3（分析 Gate）：事實/情緒分離 + 指標工具 + confidence 可用。
- G4（整合 Gate）：前端完成真資料串接且狀態完整。
- G5（驗收 Gate）：測試、文件、風險、回滾方案齊備。

---

## 5) Sprint 1 Backlog（建議 10 項）

1. 定義 AnalyzeResponse v1 schema。  
2. 建立 `/health` 與 `/analyze`。  
3. 實作 API 合約測試（成功/錯誤/邊界）。  
4. 建立 LangGraph state。  
5. 實作 `judge_data_sufficiency`。  
6. 實作 retry 與 loop guard。  
7. 新增節點事件 logging（供路徑圖顯示）。  
8. 建立 RSS connector（symbol 關鍵字抓取）。  
9. 新聞 metadata 標準化與去重。  
10. 更新進度文件與 README（同一 PR）。
11. 建立「完成即補測試」檢查清單並納入每週 Checkpoint。

---

## 6) 每週 Checkpoint 模板（可複製）

- 週次 / 日期：  
- 本週目標（1 句）：  
- 已完成（可驗收項）：  
- 未完成與原因：  
- Gate 狀態（G1~G5）：  
- 風險與阻塞（含 owner + ETA）：  
- 測試結果（單元/整合/手測）：  
- 指標快照（成功率/錯誤率/平均耗時）：  
- 下週承諾（Top 3）：  
- 文件同步確認（README / progress / breakdown）：
- 計劃文件同步確認（本週完成項目是否已回寫 plans；若原無計劃文件，是否已補產）：

---

## 7) 文件維護規範

- 進度唯一真相來源：`docs/progress-tracker.md`
- 任務唯一真相來源：`docs/implementation-task-breakdown.md`
- API 技術契約唯一真相來源：`docs/backend-api-technical-spec.md`
- 入口與操作唯一真相來源：`README.md`
- 每週五 Checkpoint 後 24 小時內同步更新三份文件。
- Gate 結果變更必須當日更新文件並註明原因。
- **每次完成「計劃文件中的任務」時，必須於當日更新對應計劃文件（`docs/plans/*.md`）的完成狀態與交付結果。**
- **若完成的需求原先沒有計劃文件，必須先補產一份計劃文件（至少含目標、範圍、DoD、完成紀錄）再標記任務完成。**
