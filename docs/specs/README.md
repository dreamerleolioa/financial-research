# Specs 目錄導覽

> 目的：降低規格文件數量，讓每份文件有明確職責。新增需求前先確認是否能放入現有文件。

## 文件地圖

| 文件                                           | 內容                                                | 何時更新                           |
| ---------------------------------------------- | --------------------------------------------------- | ---------------------------------- |
| `ai-stock-sentinel-architecture-spec.md`       | 核心分析架構、四維資料流、技術/消息/籌碼/基本面規則 | 分析架構或資料流成為長期事實時     |
| `backend-api-technical-spec.md`                | 後端 API contract、request/response schema          | API 欄位、路由、錯誤碼改變時       |
| `ai-stock-sentinel-position-diagnosis-spec.md` | 持股診斷專屬語意、出場/減碼流程                     | `/analyze/position` 行為改變時     |
| `ai-stock-sentinel-automation-review-spec.md`  | 自動復盤、歷史紀錄、資料循環                        | 自動化 review 或每日紀錄流程改變時 |
| `ai-stock-sentinel-execution-roadmap-spec.md`  | P0/P1/P2/P3 階段需求、驗收條件、否決決策            | 階段性需求或 roadmap 決策改變時    |

## 維護規則

- 不再新增零散的 `p0-*`、`p1-*`、`p2-*` spec；階段需求統一放進 `ai-stock-sentinel-execution-roadmap-spec.md`。
- 長期系統事實放進對應核心 spec，不要只留在計劃文件。
- 被否決需求需保留決策理由，但不需要獨立成檔。
- 開放問題和執行討論放在 `docs/plans/` 或 agent 對話，不放進架構事實文件。
