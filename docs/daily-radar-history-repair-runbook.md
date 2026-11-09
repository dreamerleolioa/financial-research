# Daily Radar 歷史驗證補修

此工具處理 signal-close v2 的窗口日期偏移、歷史觀察診斷缺失，以及有官方證據的休市日 run。部署與正式資料補修須另外授權；本機驗證不代表正式資料已修復。無 schema migration。

## 契約與來源

- 窗口固定為信號後第 N 個正式交易日；日期來自完整月份的 TWSE 指數報表，保存來源 URL、擷取時間、雜湊及覆蓋區間。缺價格不能把目標日順延；5／10／20／40／60 日維持完整窗口。
- prepare 先查市場開休市並保存證明；scoring 只讀 DB 證明。部署前建立的 prepared run 若缺證明，須重新 prepare 或經審閱補入官方日曆證明，scoring 才可繼續。
- 休市 run 標記 `invalidated`，以既有 errors JSON 保存原 status 與修補識別；保留全部候選、來源與已存結果。公開清單、歷史校準、規則報告、兩種 validation 與首次觀察起點排除該日所有 revision，避免回取同日舊版本。
- 觀察診斷使用原始 candidate 的支撐、壓力、收盤價及策略版本。補取 adjusted 行情必須包含信號日，並與保存的信號收盤價符合相同基準；不可將官方 unadjusted 個股行情直接混入、任意換算或抹除衝突。
- diagnostic 操作僅補診斷及稽核欄位，保留原報酬；recompute 同時重算目標日、報酬、MFE／MAE 與其他價格衍生欄位。缺失／衝突／價格基準不符時仍列為缺口，保存可行動原因。修補後不足的診斷可用新證據再次補修。
- 已有 historical_repair 證據的窗口由此工具重試，普通 due 流程不覆蓋；不改動 next-open v2 的歷史結果或企業行動證據。
- 一般持久化入口也在鎖內重新檢查 historical_repair 所有權，拒絕較早啟動的補跑、range 或一般寫入 CLI 覆蓋補修結果。診斷的首次觀察歸屬納入 manifest 前置條件；公開歷史改變時須重新盤點與建立 plan。休市日排除而晉升為首次觀察的信號，既有 not_first_observation 診斷會重新列為待補修，讀取端也保留缺口。
- 公開讀取優先使用完整、來源可追溯的日曆，將偏移窗口以 `calendar_invalid_windows` 和 skipped reason 揭露，避免污染績效；未成熟窗口仍按日曆計數。沒有可信日曆的舊資料維持保守判定。
- 同日較短範圍的刷新保留既有較完整日曆；重疊區間的交易日期互相矛盾時拒絕寫入。日曆覆蓋尚未到 report 的 as_of_date 時，不將缺結果宣稱為未成熟或 coverage 完整。
- 日曆證據使用 `twse-session-calendar-v2`，保留未裁切的官方月報日期，與範圍內的交易日逐日核對；價格數值缺失不影響日期解析。要求日期同日或之後的已發布交易日作為尾端證據，必要時多取一個月。尾端尚未發布時維持 unknown／retryable，休市日可能須等待下一交易日的月報。舊版證據不再證明完整覆蓋，需重新收集；既有價格與驗證結果不自動改寫。跨日期的重疊衝突在接受新日曆前拒絕，既有衝突須先經唯讀盤點與審閱補修。
- readiness 接受 terminal 前依原因核對證據：候選價格日期原因須仍符合目前快照；終止交易須有匹配的生命週期證明；next-open 的配股、減資與分割須符合完整窗口、target、價格 basis 與事件來源。分割事件不會略過窗口後段的資料檢查。舊結果缺證據或與目前資料不符時列為 `terminal_evidence_unverified`／待審閱，不自動覆寫歷史結果。

## 分階段操作

從 `backend/` 執行。`DATABASE_URL` 由受控環境提供，不寫入檔案、命令參數或報告。inventory／plan 使用唯讀交易；capture 在 DB session 關閉後才連外。所有輸出使用新檔名，保留前次證據。

先擷取並審閱日曆，盤點截至固定日期的結果：

```sh
uv run python scripts/repair_daily_radar_history.py calendar --start-date 2026-06-01 --as-of-date 2026-11-05 --output /tmp/radar-calendar.json
uv run python scripts/repair_daily_radar_history.py inventory --calendar /tmp/radar-calendar.json --output /tmp/radar-inventory-before.json
uv run python scripts/repair_daily_radar_history.py plan --calendar /tmp/radar-calendar.json --request /tmp/radar-inventory-before.json --only-invalid-runs --output /tmp/radar-quarantine-plan.json
```

第一份 manifest 只隔離有官方休市證據的 run，並補入 prepared run 的日曆證明。審閱其完整 before／after、所有 revision、SHA256，保存 manifest 作為目標資料備份；取得正式寫入授權後，才使用下方 apply。完成後重新 inventory，讓首次觀察起點符合新的公開範圍。結果批次的 plan 強制要求共用日曆與市場證明已保存且符合本次證據，避免多批 manifest 同時擁有尚未套用的 metadata 前置值，造成互相阻擋回復。

```sh
uv run python scripts/repair_daily_radar_history.py inventory --calendar /tmp/radar-calendar.json --output /tmp/radar-inventory-after-quarantine.json
uv run python scripts/repair_daily_radar_history.py capture --request /tmp/radar-inventory-after-quarantine.json --offset 0 --limit 250 --output /tmp/radar-prices-0000.json
uv run python scripts/repair_daily_radar_history.py plan --calendar /tmp/radar-calendar.json --request /tmp/radar-inventory-after-quarantine.json --evidence /tmp/radar-prices-0000.json --offset 0 --limit 250 --output /tmp/radar-results-0000-plan.json
```

inventory 是固定的完整清單；每批至多 250 個不同結果列。後續批次使用同一 inventory 的 offset 250、500 等，capture 與 plan 使用相同 offset／limit。每批套用後才建立下一批 plan，以取得當下的前置值；不可對逐漸縮小的新 inventory 沿用舊 offset。捕捉失敗會保留 provider failure，缺值不會轉成成功。

plan 預設只有 SELECT，不修改 DB；manifest 保存結果前後值、candidate 雜湊、休市 run 前後值與日曆 metadata 前後值。確認數量、目標日期、診斷不足原因及未修復窗口；不要只以 missing_diagnostic_count 歸零判定完成。

## 套用、回復與驗收

以下指令會寫 DB，須先取得授權。SHA256 必須是已審閱 manifest 頂層的 `sha256`；不能以臨時重算未審閱清單的雜湊取代確認。

```sh
uv run python scripts/repair_daily_radar_history.py apply --manifest /tmp/radar-results-0000-plan.json --apply-sha256 REVIEWED_SHA256 --output /tmp/radar-results-0000-receipt.json
uv run python scripts/repair_daily_radar_history.py rollback --manifest /tmp/radar-results-0000-plan.json --apply-sha256 REVIEWED_SHA256 --output /tmp/radar-results-0000-rollback-receipt.json
```

套用／回復先對 run 與 candidate 表取得 `SHARE ROW EXCLUSIVE`，暫停新的歷史發布與候選變更，保留一般讀取；接著依序鎖定 run、結果、candidate 與 prepared metadata，核對全部 preimage 與首次觀察歸屬後才寫入。結果列與一般 validation writer 使用相同鎖定順序。任一資料已變動就拒絕整批。設定 lock timeout 5 秒、statement timeout 30 秒，交易內不連 provider；既有發布若尚未完成，補修可能因鎖定逾時而拒絕，須重新唯讀核對後再嘗試。鎖定語意依 [PostgreSQL 明確鎖定規格](https://www.postgresql.org/docs/current/explicit-locking.html)。單批結果與稽核一併提交，重複套用／回復具冪等性。程序中斷時，manifest 仍保有前後值，可用相同 manifest 重新核對與執行；空白 receipt 不代表成功。

依套用順序反向回復各批，最後回復隔離／日曆 manifest。若其他已修補窗口仍依賴該日曆，工具拒絕移除共用 metadata，須先回復相依批次。工具不刪除 run、候選、raw data 或既有 next-open 企業行動證據。

每批提交後唯讀核對精確 IDs、before／after、官方窗口長度及信號日期；全部完成後重新 inventory，分別報告日期偏移、缺診斷、診斷 insufficient_data、成熟缺結果與可重試缺口。核對兩種 validation 的 5／10／20／40／60 日、scoring 日期／數量及 next-open 既有企業行動證據。任何剩餘行情缺失或價格基準衝突都保持可見，不以 HTTP 成功或 cursor 結束宣稱 coverage 完整。
