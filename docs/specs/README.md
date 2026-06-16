# Specs 目錄導覽

> 目的：降低規格文件數量，讓每份文件有明確職責。新增需求前先確認是否能放入現有文件。
> 最近同步：2026-06-12。`docs/plans/` 目前不作為長期架構來源；已落地或決策完成的內容要沉澱回本目錄。

## 文件地圖

| 文件                                           | 內容                                                | 何時更新                           |
| ---------------------------------------------- | --------------------------------------------------- | ---------------------------------- |
| `ai-stock-sentinel-architecture-spec.md`       | 目前系統架構、模組邊界、四維資料流、Daily Radar / shared context / portfolio lifecycle 的長期事實 | 分析架構、資料流、模組責任或跨功能邊界改變時 |
| `frontend-architecture-spec.md`                | 前端技術棧、provider/route 邊界、server state policy、Portfolio Query/Mutation 架構、Zod API boundary validation | 前端技術棧、資料流、state ownership、query key 或 API boundary validation 改變時 |
| `backend-api-technical-spec.md`                | 後端 API contract、request/response schema、內部 API 與錯誤碼 | API 欄位、路由、錯誤碼或 internal workflow contract 改變時 |
| `daily-stock-radar-spec.md`                    | Daily Radar product/API/data-flow spec、universe、scoring、request budget、背景 context 與 validation/governance | Daily Radar universe、scoring、data-flow、schedule、shared context 或 public read behavior 改變時 |
| `ai-stock-sentinel-position-diagnosis-spec.md` | 持股診斷專屬語意、出場/減碼流程、結案復盤與 lifecycle review 邊界 | `/analyze/position`、portfolio review 或 lifecycle review 行為改變時 |
| `ai-stock-sentinel-automation-review-spec.md`  | 自動復盤、歷史紀錄、資料循環、每日分析與 cache/ledger 行為 | 自動化 review、每日紀錄或歷史資料循環改變時 |
| `ai-stock-sentinel-execution-roadmap-spec.md`  | P0/P1/P2/P3 階段需求、驗收條件、否決決策 | 階段性需求、release gate 或 roadmap 決策改變時 |

## 維護規則

- 不再新增零散的 `p0-*`、`p1-*`、`p2-*` spec；階段需求統一放進 `ai-stock-sentinel-execution-roadmap-spec.md`。
- 長期系統事實放進對應核心 spec，不要只留在計劃文件或 agent 對話。
- API 欄位的正式 contract 以 `backend-api-technical-spec.md` 為準；其他文件只描述語意、流程與邊界，避免複製完整 schema 後漂移。
- Daily Radar 的 ranking/score/bucket、shared context、forward validation 與 monthly rule governance 以 deterministic backend workflow 為準；文件不得把它描述成 LLM 選股。
- `shared_background_contexts` 是背景 evidence/cache，不是 action/ranking/verdict/classification 的決策覆寫來源。
- 被否決需求需保留決策理由，但不需要獨立成檔。
- 開放問題和執行討論可放在短期計劃或 agent 對話；只有完成決策或落地行為才更新本目錄。
