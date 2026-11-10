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
- 持久日曆收集共用工作執行器的來源 store，新抓取與快取命中的每份月報都記錄來源參照，接受前一併核對來源版本。收集後月報版本改變時拒絕接受並續跑，不保存該份日曆；來源忙碌時保留共用依賴資訊。維持既有請求／時間預算、快取與已接受日曆的重用規則，不增加第二層 HTTP 包裝或另開 domain 寫入交易。
- readiness 接受 terminal 前依原因核對證據：候選價格日期原因須仍符合目前快照；終止交易須有匹配的生命週期證明；next-open 的配股、減資與分割須符合完整窗口、target、價格 basis 與事件來源。分割事件不會略過窗口後段的資料檢查。舊結果缺證據或與目前資料不符時列為 `terminal_evidence_unverified`／待審閱，不自動覆寫歷史結果。
- 持久工作在 scoring 尚未完成時仍稽核實際前置資料；缺口只重排受影響的 refresh／symbol／法人市場，並讓 scoring 等待完整依賴鏈恢復。重用 prepare 時會接回既有歷史補抓依賴；缺少依賴紀錄列待審閱。重抓的開市證據只在準備版本仍相符時更新 prepared row，保留並行產生的有效證據。前置稽核不掃描全年驗證結果，也不代表 validation 完整。同日不同 lanes 的整合使用獨立的短交易鎖；網路收集在交易外。狀態更新保留其他 worker 的 lease；來源的 Retry-After 在等待依賴時仍保留。所有必要工作完成後，仍須通過完整 readiness 稽核。
- 窗口計畫追蹤候選的驗證輸入版本；只補顯示名稱不重建驗證身份。執行前與接受結果前均核對輸入，失效工作立即重新規劃。結果修補使用獨立的 50 分鐘盤點時鐘，持續推進工作不會延後盤點；下一次實際推進才執行到期盤點。此機制不自動改寫已保存的歷史結果。
- 兩種 validation 的新結果保存 `radar-validation-input-v1` 候選版本證明；價格、指標、策略或首次觀察起點改變後，不沿用舊版本的已驗證報酬／診斷。readiness、窗口登錄及公開讀取共同將缺少或不符證明的已驗證結果列為 `validation_input_unverified`，保留原始資料，不計入公開績效，不自動重寫或補造當前版本證明。顯示名稱與未使用的歷史／replay blob 不影響版本。舊 terminal 沒有報酬可認證，仍須通過前述原因別證據契約；已有版本證明時也須相符。
- 歷史補修只有完整 `recompute` 可建立新的報酬輸入版本證明；只補診斷的操作保留原報酬與既有或缺少的證明。signal-close 的 inventory 也會將缺證／版本不符的已保存結果列為 `recompute`，即使 target 日期正確；已有相符證明且 target 正確的結果不重算。manifest 凍結完整候選驗證版本，apply／rollback 在既有鎖內重查，包含舊候選快照未涵蓋的評分等欄位，任何漂移先拒絕整批。正式資料的缺證盤點、補修 manifest、apply／rollback 均維持各自的審閱與授權，不因程式更新而自動執行。
- 持久工作分頁登錄完整的 365 日候選清單，每次整合至多處理 5 個候選、兩種 basis 合計 50 個窗口；保存輸入版本與游標，未登錄完畢前不宣告 intent 完成。只剩登錄工作時仍標記 runnable，讓既有推進器續跑。每頁完成前重查候選與日曆版本，變動時保留工作歷史、停用該 intent 的舊窗口連結，再從頭盤點。盤點時鐘保留第一頁的開始時間，避免末頁完成後再延後已修復結果的重新檢查。缺日曆時暫停窗口並重新排入日曆工作；證據衝突維持待審閱，須另行確認修復與重排，不因讀得到覆蓋日曆就自動清除。PostgreSQL 在 DB 端彙整版本雜湊，避免一次傳回全部候選快照；版本查詢仍掃描必要的歷史來源，不代表總查詢成本固定。公開 inventory 預設保留完整清單，最終 readiness 仍核對兩種 basis 的全部 5／10／20／40／60 日窗口。

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

## 受保護的 next-open 舊結果

一般 writer 保留企業行動與人工補修的覆寫保護。若舊 next-open 報酬缺少有效 `validation_input`，或已受保護的結果需要重算，使用獨立的 `repair_daily_radar_next_open.py`，不要手動刪除舊證據或放寬 writer。先完成上述共用日曆階段，並確認 cutoff prepared row 保存完全相同的日曆；此入口不改日曆、不隔離 run、不改 signal-close。

```sh
uv run python scripts/repair_daily_radar_next_open.py inventory --calendar /tmp/radar-calendar.json --output /tmp/next-open-inventory.json
uv run python scripts/repair_daily_radar_next_open.py capture --calendar /tmp/radar-calendar.json --request /tmp/next-open-inventory.json --offset 0 --limit 50 --output /tmp/next-open-capture-0000.json
uv run python scripts/repair_daily_radar_next_open.py plan --calendar /tmp/radar-calendar.json --request /tmp/next-open-inventory.json --evidence /tmp/next-open-capture-0000.json --offset 0 --limit 50 --output /tmp/next-open-plan-0000.json
```

capture 使用自己的唯讀 session，先凍結相關官方 archive，再於 provider I/O 前釋放交易；完整 Yahoo 行情仍須與該 archive 交叉檢查。封存行情、archive、官方報告呼叫結果及輸入前置值，不寫 DB。plan 只重播封存資料，沿用現行 next-open、現金增資與企業行動驗證，完整檢查各筆 5／10／20／40／60 日窗口；有缺價、來源衝突或證據不足時拒絕整批，不會產出可套用的成功 manifest。失敗 capture 不等於資料 ready。預設每批 50、上限 250，capture／plan 須使用相同固定 inventory 與 offset／limit。輸出檔採 0600 權限且拒絕覆寫。

新版取證必須另有完整官方股票與 TAIEX 窗口（含信號日），不能因 Yahoo 完整或 archive 為空而省略。過往現金增資／配股／減資證據即使藏在 `historical_repair.prior_evidence`，仍須重新通過既有事件、價格、精度與完整窗口判定；停牌缺日只接受同一官方事件已證明的 halt 範圍。來源不足時 preview 留下 `next_open_official_evidence_incomplete` 與 `official_repair_error.code`，plan 拒絕整批。

現金增資補修另可認證 Yahoo 原始行情已與官方全窗口一致的情況，包含既有容差內的精度差異。此分支仍重新查核現金增資條件、參考收盤價、其他公司行動與完整官方股票／TAIEX 行情，使用官方未還原價格計算；`verification_mode` 區分 `raw_prices_matched` 與 `adjustment_recovered`，取證與離線重播採同一模式。一般增資衝突 recovery 仍要求實際可還原價差；舊取證沒有此模式時維持原判定。

日曆修正後，若同一類舊事件（包括巢狀 prior evidence）都已核實位於新窗口外，補修以 `prior_action_scope` 保存日期、標的、範圍及新取得的官方報告。取證須重新核實舊事件，並完整檢查新窗口的行動報告與股票／TAIEX 行情；停牌以停止至恢復買賣的整段區間判斷，與窗口重疊時仍保留原阻擋。未知日期、標的或來源不能取得窗口外豁免。新窗口若另有事件，須通過其現行判定；現金股利仍採未還原價格報酬並核對除息資料，不納入報酬。只有舊分割證據的窗口也可列入人工補修 inventory，普通 writer 的保護不變。

`prior_action_scope` 的原始報告、來源 hash 與範圍判定由 plan／apply／rollback 離線重播，不會在寫入交易內連 provider。完整窗口、前後值、候選 revision 與舊證據鏈繼續保留；需要窗口外判定時，缺少此證據的 manifest 不可向前 apply，須重新 capture，不能只改版號或重算雜湊補入證據。已套用的舊 v2 manifest 保留原嚴格判定及受控 rollback；其他不需要窗口外判定的既有 manifest 沿用原檢查。

全範圍與單日報告必須一致，不能用單日查到事件掩蓋全範圍缺漏；事件類型、條件、參考價及停牌區間都須互相核對。舊分割不會只憑「權」字當作已核實，配股須比對既有有界精度下的換股比率；獨立面額變更記錄則保留完整資料列交叉核對（[TWSE 面額變更說明](https://www.twse.com.tw/zh/announcement/change/cal.html)）。目前增資／配股／減資的專用驗證失敗時，不可用通用 `stock_split_in_window` 覆蓋；此補修分支的通用分割僅接受獨立官方面額變更佐證。一般現金股利另核對官方前一交易日收盤價、金額與既有 Decimal 容差，不接受 Yahoo 與官方行動資料互相矛盾。

通用分割也須核對整個窗口的股利與分割標記，不能只核對第一筆事件；未記載股利、額外分割或 benchmark 的未知行動都保持未解狀態。benchmark 會同時檢查實際計算列與事件重播的原始輸入，避免轉成官方價格時把矛盾標記消掉。

一般 readiness、validation 報表／窗口排程與 durable 窗口摘要在 SQL 層排除 `reviewed_official_window` 與 `historical_repair.prior_evidence`，不縮短年度範圍或窗口，也不移除目前輸入認證、事件／terminal 判定及補修識別。job status 只投影工作狀態所需欄位，不載入完整工作結果。完整原始證據仍保存於 DB 與 manifest；補修、寫入 revision／前置值檢查及 rollback 繼續使用完整快照。

目前 capture 與 manifest 契約為 v2，manifest 的 `reviewed_official_window` 保存完整官方價格、來源及實際計算輸入，沿用既有浮點容差。必要事件另保存原始報告與行情供離線重播；plan 與 apply 都重新執行窗口、標的、來源、事件、精度及報酬驗證，apply 交易內不連 provider。事件重播輸入由候選、窗口及行情重新建立，不採用 manifest 可改寫的狀態與報酬欄位。既有分割事件也不能因後來的行情漏標而變回已驗證報酬。v1 capture 必須重新取證，v1 manifest 禁止 apply，不能只改版號或重算 SHA256 延用舊證據。已套用的 v1 manifest 保留 rollback，仍須通過原雜湊、輸入版本、前後值與共用日曆檢查。

審閱 manifest 的完整前後值、候選 revision、官方事件與來源證據及 SHA256，另取得正式寫入授權後才執行：

```sh
uv run python scripts/repair_daily_radar_next_open.py apply --manifest /tmp/next-open-plan-0000.json --apply-sha256 REVIEWED_SHA256 --output /tmp/next-open-receipt-0000.json
uv run python scripts/repair_daily_radar_next_open.py rollback --manifest /tmp/next-open-plan-0000.json --apply-sha256 REVIEWED_SHA256 --output /tmp/next-open-rollback-0000.json
```

apply／rollback 共用既有補修的鎖定、雜湊、版本、candidate revision 與全批 preimage 檢查。新結果的 `historical_repair.prior_evidence` 保留原始 outcome，manifest 另保存完整原列供回復；不把舊企業行動證據直接當成新報酬證明。回復後仍可能需要人工處理，必須重新檢查完整 365 日 inventory、兩種 basis 與所有窗口，而非只看本批 receipt。

完整窗口另保存 `validation_window` 輕量指紋，包含 basis、信號日、窗口長度、進場日、目標日及實際計算交易日序列的 hash；readiness、公開驗證結果及續跑核對整段日期，不因目標日相同就延用舊結果。舊補修可使用已保存的完整官方窗口日期，摘要 SQL 僅投影小型日期清單；沒有完整日期證據時列為待核實，不由目標日或價格稀疏程度推算。日曆變更後，受保護結果進人工補修，一般 writer 仍不得覆蓋。

向前 apply 另驗證 `historical_repair` 擁有權：repair_id、operation、calendar hash 與完整 before hash 必須對應同一份 manifest；next_open v2 的版本、capture hash 及 `prior_evidence` 也必須一致。移除、清空或改寫標記後即使重算根雜湊仍拒絕整批。標記以原 canonical fingerprint 核對；持久化前後快照另採符合 JSONB 數值語意的 fingerprint，允許 `-0.0`／`0.0` 及同值數字表示的正規化，仍區分 JSON 布林、數字與字串，避免把 `true` 與 `1` 視為同一份證據。來源、capture 及 before 證據雜湊維持原規則。舊版已套用的資料可受控 rollback，但仍須通過原有雜湊、candidate revision、來源／日曆及精確前後快照檢查，不替舊資料補造新證據。
