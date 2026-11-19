# Financial Research 專案指引

## 開工與完成

- 新需求先做唯讀調查，說明目前行為、修改範圍、理由、順序與驗證方式。除非使用者明確免除確認，方案確認後才修改檔案或執行會產生檔案的命令。沿用已確認的方案，實質變更方向或範圍時再確認。
- 保留 WIP。行為變更先建立相關回歸保護，修正後檢查完整 diff 與受影響路徑，完成一次乾淨 review。不要為純文案或指令文件新增措辭鏡像測試。
- 命令與版本以目前 manifest、CI、測試設定為準；收尾區分已驗證、推論與未驗證。必要檢查通過後不無故重跑。
- 依當次任務與適用的全域指引判斷交付授權。已授權建立或更新 PR 時，完成必要本機檢查與 review 後，提交本次變更、推送任務分支並交付 PR。只要求 review 時在對話回報，approve、合併、手動部署／發布與其他對外發訊各需明確授權。
- 子代理只承接有用的獨立範圍並遵守目前環境的委派限制；不空輪詢、不為狀態重述喚醒 reviewer。僅遇到等待異常或明確稽核需求時查本回合必要事件，不固定解析 session。

## 架構與資料完整性

- FastAPI／Python 後端在 `backend/src/ai_stock_sentinel/`，React／TypeScript 前端在 `frontend/`。按功能追 router → service／provider → repository／DB → presenter → frontend，先確認責任層再修改。
- Daily Radar 的工作結果必須核對同一 `run_date`、必要 refresh step、資料 provenance 日期、missing reasons 與 scoring output；HTTP／workflow 綠燈不能單獨代表資料 ready。
- 讀取與診斷預設唯讀。重跑 production refresh、backfill 或寫入資料庫須有具體授權；有界恢復保留 provenance、cursor、error 與資料完整性檢查。
- 不把 provider 缺值改成 0 或以寬鬆 fallback 當作成功；區分有效資料不足、provider／transport 失敗與不適用。調整分類時先建立相關 fixture／regression。
- Portfolio／cache 修改核對 user、symbol、交易日與 mutation owner，避免跨用戶或過期請求提交資料。
- 推送或合併至 `main` 會觸發 CI/CD。正式 API 啟動時以 `DATABASE_MIGRATION_MODE=verify` 驗證 schema。發布經 `gcp-production` 核准後先比對資料庫與映像的 Alembic revision。已達 head 就部署，僅落後一個符合線上遷移條件的 revision 時，由獨立 migration Job 更新，再次確認已達 head 才部署。檢查失敗或不符合條件時停止發布。需要人工遷移時，依[正式 schema 遷移手冊](docs/gcp-schema-migration-runbook.md)另行核准、備份與驗證還原。

## 按需讀取

- 入口與部署契約：[README](README.md)。Radar 行為：[規格](docs/specs/daily-stock-radar-spec.md)。自動化與發布 gate：[自動審核規格](docs/specs/ai-stock-sentinel-automation-review-spec.md)。
- Radar／EPS backfill／margin／AVWAP 維運使用 `financial-research-operations`；其餘功能依目前程式、CI 與規格判斷。

## 驗證

- 後端從 `backend/` 跑 `uv run pytest <affected-test-files> -q`；後端完整 CI gate 為 `uv run pytest tests/ -v`。
- 前端從 `frontend/` 跑 `pnpm build`（含 TypeScript 檢查）與 `pnpm lint`；可見行為依相關 Playwright spec 驗證。
- 維運診斷報告要列 requested/effective run_date、step readiness、來源日期、缺資料原因與仍未驗證部分；不得因 cursor 結束或 HTTP 成功宣稱 coverage 足夠。
- 指令文件修改驗證本地引用、manifest 命令及工作流一致性，不啟動 production API 或 migration 來驗證文案。
