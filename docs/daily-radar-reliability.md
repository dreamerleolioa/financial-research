# Daily Radar 完整度與續跑契約

Daily Radar 的執行狀態、當日發布、兩種歷史 validation 與輔助資料分別判讀。HTTP 成功、工作已排入或 cursor 結束，都不能單獨代表資料完整。

## 第一階段：完整度查詢

`GET /internal/daily-radar/readiness?market=TW&run_date=YYYY-MM-DD&lookback_days=365` 使用既有 internal token，純讀 DB，不呼叫 provider。日期必填；lookback 允許 1–1095 天，回應明列樣本起始日。回應包含：

- `scoring`：目前 prepared inputs 能否通過必要 refresh、開市證據、同日完整 raw data 與 market context 檢查。合法且有證據的短歷史仍交給既有 prefilter 排除。
- `publication`：該日 canonical public run、狀態及候選數。已發布不代表目前待重算輸入或歷史 validation 完整。
- `validation.signal_close` / `validation.next_open`：逐一盤點樣本範圍內 canonical candidates 的 5、10、20、40、60 日窗口，區分已驗證、有效 terminal、未成熟、未知成熟度、缺結果、日期錯誤、待補及缺診斷。next_open 僅含 selected 與 comparable shadow，沿用既有研究範圍。
- `auxiliary`：AVWAP 與 managed raw data 的獨立狀態。兩者不是 scoring 必要 refresh。
- `data_ready`：當日 completed publication、目前 scoring prerequisites 及兩種 validation 都完整。不是所有歷史日期的無範圍保證，也不包含 auxiliary 完整度。

沒有完整涵蓋樣本的官方交易日證據時，缺失窗口是 `unknown_maturity`；不以價格序列的筆數證明未成熟。signal_close 已 validated 的首次觀察仍核對診斷版本、origin 及診斷是否可用；repeat 不重複要求首次觀察診斷。`gaps` 最多回傳 100 筆，計數涵蓋完整樣本並標明截斷。

Workflow 以不同 lane 回報 publication、signal_close、next_open；repair 最後仍對整體完整度檢查。可讀取摘要與保存的 JSON 追查缺口，而非將 validation 缺口誤判為當日 scoring 失敗。

來源延遲與 transport 失敗等待來源；預算用盡續跑；價格衝突保留證據待裁決；缺診斷與日曆問題走指定修復。不能將未知資料改成 terminal 來取得完整狀態。`adjusted_history_changed` 提供第一筆衝突日期、欄位及兩側價格；完整歷史檢查與容差不變。

## 第二階段：來源快照與輸入版本

新增 additive migration `cd4e5f6a7b8c`：`radar_source_snapshots`、`radar_input_manifests`、`radar_run_inputs`。備份 allowlist 同步納入。預設 `DAILY_RADAR_SOURCE_SNAPSHOTS_ENABLED=false`；必須完成正式 migration、後端部署及核准的切換後才啟用。

官方報表、Yahoo 公司行動回應及標準化未還原研究行情以 request、SDK／擷取版本、payload hash、擷取與到期時間保存。有效快照跨 HTTP 請求重用十分鐘；到期重抓產生新觀測，不覆寫原內容。擷取不代表資料完整：空報表、日期／標的錯誤仍由既有 consumer 拒絕，transport 失敗不保存成空資料。原始回應不保存認證參數。官方行情仍是未還原價格，不能直接取代技術指標的還原價格。

技術計算實際採用的還原 OHLCV 歷史另存快照，包含完整 OHLC 與缺值；既有補抓、還原基準與整段歷史核對保持不變。歷史已有的缺診斷資料不因啟用此功能而自動補齊。

DB-only scoring 在計算前凍結 records、market context、背景籌碼、歷史觀察、AVWAP、策略版本、候選上限與 prepared context。manifest 與 run 關聯和 publication 同一交易提交。相同輸入只重用目前 canonical completed run；舊修訂不能復活。來源／輸入變更建立新 manifest 與新 run，原結果保留。與歷史 repair 採一致的 relation → row 鎖定順序，外部 HTTP 不在 publication 交易內執行。

readiness 在開關啟用後比較目前 inputs fingerprint 與已發布 manifest，並核對實際 selected candidate 與整份輸出的筆數。輸出缺列時不能重用原 run，會保留舊結果並重建新修訂。舊發布沒有 manifest 時明列 `legacy_unrecorded`；不拿今天資料替舊結果補造來源證明，也不宣稱新輸入已發布。查詢仍完全唯讀。單次 `/run` 也先擷取 technical evidence，再寫 AVWAP／籌碼，避免小連線池在來源保存時耗盡。

來源快照及 manifests 不提供更新／刪除 API，讀取時驗證 hash；來源資料修訂只能新增觀測。正式回滾優先關閉開關並保留證據表。Migration downgrade 僅供隔離驗證；正式刪表會刪除證據，需要另外的資料處置授權。

## 核准的第三階段

第三階段保存來源依賴、待辦與嘗試紀錄，短交易領取工作、外部 HTTP 不占用交易，以到期時間與執行權版本控制有界續跑及冪等提交。沿用 Actions、Cloud Run、Cloud SQL，不新增 queue service。GitHub concurrency 僅負責互斥，不能取代持久工作紀錄。

正式 migration、部署及歷史補修分別授權。歷史補修沿用 `daily-radar-history-repair-runbook.md` 的 manifest、前後像、CAS 及 rollback 保護；部署不會自動補寫歷史結果。
