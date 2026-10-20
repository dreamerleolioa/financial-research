# 2026-10-15 Daily Radar：原始結果與事後 EPS 補齊說明

本文件是資料恢復說明，不是重新產生的 10/15 候選名單或績效結果。查核使用既有唯讀角色與 `transaction_read_only=on`，未呼叫 production refresh／backfill、未更新正式資料，也未修改 `first_observed_at`。主要查核時間：2026-10-16 10:41 Asia/Taipei；融資完整度另於同日上午覆核。

## 原始結果保留

Requested／effective `run_date` 均為 **2026-10-15**，原始 run **162** 保持 `completed`：164 檔 universe、83 檔通過預篩、83 個候選。81 筆紀錄全部是 `prefilter_rejected`，沒有 run／candidate 執行例外。

同日期的 `refresh-institutional-flows`、`refresh-lending`、`refresh-full-margin`、`refresh-ohlcv`、`refresh-market-context` 均為 `completed`；AVWAP 與 managed raw-data step 亦完成。OHLCV 與 AVWAP 的 requested／effective 日期均為 10/15。本文未另外重做各官方來源的逐列下載比對，也未重跑 scoring 或 validation。

AI evidence 的 technical、institutional、margin lane 皆無缺漏；fundamental lane 保留 `2355.TW`、`3227.TWO`、`5009.TWO`、`5521.TW`。因此，`refresh-ai-evidence.status=completed` 只表示當次 materialization 已執行，repair monitor 仍正確回報不完整。

依匯出時的必要欄位檢查（包含 price history），**160／164 檔符合分析資料門檻，4 檔基本面不足**。這是資料完整度，不是股票排名，也不表示 160 檔都是候選。四檔須保留在完整 raw pool 中列為資料不足，不能加入 10/15 的 AI shortlist。

附帶覆核：`5475.TWO` 的原始融資餘額、變動與 `margin_to_volume` 都為 0，並有 `margin_delta_pct_unavailable_reason=baseline_zero`。匯出端原先要求百分比數值，造成額外的保守排除；本次統一使用後端既有 `margin_evidence_is_complete` 判準，保留「基期為零所以百分比無法計算」的原因，不補造百分比。這項修正只影響匯出完整度判定，未修改資料或 scoring。

## 四檔事後補齊狀態

截至 10/15，每檔只有一個可見季度：2026 Q2 累計 EPS，缺少能推導單季 EPS 與四季 TTM 的歷史。歷史 EPS 到 10/16 上午才首次入庫；目前每檔有 54 個季度。

| 股票 | 10/15 可見季度數 | 歷史 EPS 首次入庫（台北時間，10/16） | 事後 TTM EPS |
| --- | ---: | --- | ---: |
| 2355.TW | 1 | 09:18:42 | 1.90 |
| 3227.TWO | 1 | 09:18:44 | 12.24 |
| 5009.TWO | 1 | 09:18:44 | 2.20 |
| 5521.TW | 1 | 09:18:45 | 0.96 |

TTM EPS 單位為元／股，依查核當下版本庫的單季 EPS 相加，不是 10/15 當時已保存的結果：

| 股票 | 2025 Q3 | 2025 Q4 | 2026 Q1 | 2026 Q2 | 合計 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2355.TW | 0.19 | 0.64 | 0.62 | 0.45 | 1.90 |
| 3227.TWO | 3.01 | 3.12 | 2.65 | 3.46 | 12.24 |
| 5009.TWO | 0.30 | 0.90 | 0.50 | 0.50 | 2.20 |
| 5521.TW | 0.23 | 0.15 | 0.13 | 0.45 | 0.96 |

Q3、Q4 與 Q1 的單季 EPS 來自 `mops_historical`；Q2 依 `official_openapi` 累計資料及相鄰季度推導。歷史資料保留 `availability_quality=historical_unknown`、`source_report_date=null`。原季度日期早於 10/15，不能證明查核當下看到的財報版本在 10/15 前已公開，也不能證明當時系統已取得該版本。

## 使用與恢復邊界

- 原始 10/15 raw rows、run 162、候選與績效資料保持原樣；不回寫四檔 TTM、不回填觀測時間，也不將 repair monitor 改成成功。
- 上表可作為 **10/16 取得的事後補充**，不得加入 10/15 當時的 AI 候選選擇或績效驗證。
- 其餘通過全部必要欄位檢查的資料仍可供研究；本文未執行新的 AI 選股報告。
- 單純再跑相同 backfill 或 refresh，無法消除「首次觀測晚於 run_date」的限制；應回報該原因，避免無效重抓。
- 若要重建 10/15 已公開可知的財報內容，必須另外取得可識別公告日與修訂版本的官方原始證據；本次未完成這項外部公告驗證，因此不宣稱已恢復完整歷史快照。即使取得，事後重建也須與原入庫快照分開。
- 使用者提供的兩種 validation 結果本輪 `failed_return_bases=[]`，但仍保留歷史 `stale_candidate_price`、`stock_split_in_window` 跳過紀錄；本文未將它們視為已補齊的歷史驗證。

## 可追溯來源

- [原始 repair workflow](https://github.com/dreamerleolioa/financial-research/actions/runs/38833105350)：10/16 上午對 10/15 補修，因四檔基本面缺漏失敗。
- [基本面更新與回補 workflow](https://github.com/dreamerleolioa/financial-research/actions/runs/38834392752)：10/16 09:18 執行；本文的補齊數值另以唯讀 DB 查核確認。
- [歷史可見性與 EPS 版本選擇](../backend/src/ai_stock_sentinel/data_sources/fundamental/repository.py)、[TTM 計算](../backend/src/ai_stock_sentinel/data_sources/fundamental/official_provider.py)、當時的 `export_codex_daily_radar.py` 匯出完整度（已於 2026-10-20 隨晨間 AI 報告退役）。

## 後續流程稽核與修正範圍（2026-10-16）

本節為原查核後的補充，不替換上述原始結果、160／164 的當時完整度判讀或四檔事後 EPS 表。此次六項修正為：融資同日來源檢查；job 只納入回報的 TTM EPS 缺口；雷達執行不套用維護的七季／股利要求；AI evidence 不順帶抓取借券；validation HTTP 中斷後仍執行另一 return basis；雷達工作與一般維護依持久化用途／日期／階段隔離預算。

上述缺陷及預算風險已在本機 fixture、workflow runner 或臨時 PostgreSQL 重現／驗證，不能直接推論每項都曾發生於 10/15。新的融資日期門檻尚未用於重新計算 10/15 正式資料；原先的完整度數字仍是原查核規則下的紀錄，不宣稱已通過新版規則。

本次不呼叫 production refresh／backfill／scoring／validation，不更新 10/15 raw rows、run 162、候選、排名、step status、歷史績效或財報首次觀測時間；不將四檔 10/16 才取得的 EPS 加入 10/15 shortlist。migration 僅於臨時本機 PostgreSQL 驗證，不執行正式 migration。推送、部署與正式資料恢復均留待另行授權。

本機驗證：從 `backend/` 執行完整 `uv run pytest tests/ -v`，並啟用隔離的臨時 PostgreSQL 測試目標，結果為 **1756 passed**，僅有一項既有 Starlette 棄用警告。涵蓋 additive migration 對舊 job 的保留與唯一性、實際並行 cursor 提交、六項回歸及測試目標隔離反例。獨立 Codex review 使用既有 `gpt-6.1-sol` 設定；確認並修正一項本機測試 DSN 隔離缺口，針對性補審無新增可行動問題。這些結果不等同正式環境已套用修正或已完成即時供應商覆蓋驗證。
