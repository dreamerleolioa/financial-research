# TDCC Weekly Major Holders Follow-up Plan

## 背景

目前 `weekly_major_holders` 已由 TDCC `getOD.ashx?id=1-5` 匯入 `shared_background_contexts`，但仍有四個待修正點：

1. TDCC 持股分級的產品命名需更精準。
2. 每週 TDCC 更新範圍目前只跟最新 Daily Radar candidates 對齊，沒有穩定包含使用者當前持股與 watchlist。
3. `/portfolio/risk-summary` 尚未在 `position_risks[]` 投影 shared context 或 weekly holders 摘要。
4. 既有分析層尚未把 TDCC 千張大戶持股比例增減納入籌碼穩定性判斷。

## 待修正項目

### 1. 修正 weekly_major_holders 指標定義與命名

目前 backend 將 TDCC level `12, 13, 14, 15` 加總為 `major_holder_ratio`。這比較接近「400 張以上持股比例」，不應直接命名或顯示為「千張大戶持股比例」。

後續實作時需拆清楚：

- `thousand_lot_holder_ratio`：僅使用 TDCC level `15`，代表約 1000 張以上。
- `large_holder_400_lot_plus_ratio`：使用 TDCC level `12, 13, 14, 15`，代表約 400 張以上。
- `retail_100_lot_or_less_ratio`：使用 TDCC level `1` 到 `9`，代表約 100 張以下。

Provider 實作需把 TDCC level mapping 文件化，至少以常數註解或 payload metadata 記錄：

- level `12`：約 400 到 600 張。
- level `13`：約 600 到 800 張。
- level `14`：約 800 到 1000 張。
- level `15`：約 1000 張以上。

若要顯示增加/減少，應以不同 `as_of_date` 的歷史 `shared_background_contexts` snapshot 比較，單位使用「百分點」：

- `thousand_lot_holder_ratio_delta_pp`
- `large_holder_400_lot_plus_ratio_delta_pp`
- `retail_100_lot_or_less_ratio_delta_pp`

上一期選取規則需明確：

- 同一 `symbol`。
- 同一 `context_type = 'weekly_major_holders'`。
- 取 `as_of_date < current_as_of_date` 的最近一筆有效 snapshot。
- 忽略 `freshness = 'missing'`、payload 缺少可計算欄位、`distribution` 格式不合法的 rows。
- 若沒有有效上一期，回傳 `comparison_status = "previous_missing"`，不產生 delta。

缺少上一期資料時只顯示當期比例，不推論趨勢。

### 2. 舊資料 backfill 與相容欄位

既有 `shared_background_contexts` rows 不需要重新呼叫 TDCC。舊 payload 已保存 `distribution` 明細，應透過 Alembic migration 在部署啟動時自動 backfill：

- 僅處理 `context_type = 'weekly_major_holders'`。
- 從 `payload.distribution` 重算 `thousand_lot_holder_ratio`、`large_holder_400_lot_plus_ratio`、`retail_100_lot_or_less_ratio`。
- 補上 `holder_level_schema_version = "tdcc-holder-level-v2"`。
- 不改 `replay_key`。
- 不刪舊欄位。
- 不呼叫外部 TDCC API。
- `distribution` 缺失或格式不符時跳過，保留原資料與明確測試案例。
- migration 必須 idempotent；已存在 `holder_level_schema_version = "tdcc-holder-level-v2"` 的 rows 可安全重算覆蓋同值，或明確跳過，但不得重複產生新 rows、不得改 `replay_key`、不得依賴 `updated_at` 排序副作用。

`major_holder_ratio` 短期需保留為 legacy 相容欄位，語意維持「level 12-15 / 400 張以上」，不得改成千張以上，以免舊資料與舊 consumer 語意斷裂。新 UI、新 API projection 與後續分析應改讀新欄位。

TDCC provider 也需同步更新，讓新寫入的 `weekly_major_holders` payload 直接包含 v2 欄位，不只依賴 migration：

- `thousand_lot_holder_ratio`
- `large_holder_400_lot_plus_ratio`
- `retail_100_lot_or_less_ratio`
- `holder_level_schema_version`

### 3. 擴大每週 TDCC 更新範圍

每週 `weekly_major_holders` 更新範圍需改成：

- 全體使用者的當前 active portfolio holdings symbols
- 全體使用者的 watchlist symbols
- latest Daily Radar candidates

這三者合併後去重，再送進 `POST /internal/daily-radar/chip-context/update` 的 `symbols`。

此 symbol universe 只能用來擴大 market data refresh 範圍。`shared_background_contexts` 仍必須維持 market-only shared cache，不得寫入 user id、持股數量、成本、watchlist ownership 或其他使用者私有資料。

若其中任一來源讀取失敗，應回報明確錯誤或降級紀錄，不能靜默只更新 Daily Radar candidates，避免使用者持股長期缺少 TDCC context。

### 4. 在 portfolio risk summary 補 weekly holders projection

`GET /portfolio/risk-summary` 的 `position_risks[]` 需補上 shared context 或精簡後的 `weekly_major_holders` 摘要。

建議精簡欄位：

```json
{
  "weekly_major_holders": {
    "status": "fresh",
    "as_of_date": "2026-06-18",
    "previous_as_of_date": "2026-06-05",
    "thousand_lot_holder_ratio": 38.2,
    "thousand_lot_holder_ratio_delta_pp": 1.52,
    "large_holder_400_lot_plus_ratio": 51.58,
    "large_holder_400_lot_plus_ratio_delta_pp": 0.88,
    "retail_100_lot_or_less_ratio": 38.49,
    "retail_100_lot_or_less_ratio_delta_pp": -1.1
  }
}
```

缺資料時需遵守 shared context 原則：

- `missing` 不顯示在主要 UI。
- `missing` 不扣分。
- `missing` 不影響 portfolio risk 判斷。
- `missing` 不得被解讀為大戶未進場、散戶未增加或籌碼中性。
- `stale` 可顯示資料日期與 stale 狀態，但不得當作 fresh 訊號強化風險判斷或趨勢判讀。

### 5. 在分析層納入千張大戶增減作為籌碼穩定性指標

`thousand_lot_holder_ratio_delta_pp` 需納入 analysis / portfolio risk summary 可讀的籌碼穩定性指標，但語意必須清楚標注為「籌碼穩定」問題，而不是技術指標、Daily Radar ranking driver 或直接買賣建議。

產品定位：

- 放進系統：是，作為 `chip_stability_context` 或同等命名的籌碼穩定性 context。
- 放進 technical score：不要。
- 放進 Daily Radar ranking driver：暫時不要；可先作為 detail trace / companion context 顯示。
- 放進 portfolio / analyze 的籌碼穩定性摘要：可以。
- 放進 copy-to-AI：可以，但必須標注為「TDCC 週頻籌碼穩定性補充」，不得和技術指標分數混在同一 bucket。

建議輸出 shape：

```json
{
  "chip_stability_context": {
    "source": "tdcc_weekly_major_holders",
    "status": "fresh",
    "as_of_date": "2026-06-18",
    "previous_as_of_date": "2026-06-05",
    "thousand_lot_holder_ratio": 38.2,
    "thousand_lot_holder_ratio_delta_pp": 1.52,
    "state": "stable",
    "trend": "strengthening",
    "summary": "千張大戶持股比例增加，籌碼穩定性提升",
    "caveats": []
  }
}
```

判讀原則：

- `thousand_lot_holder_ratio_delta_pp > 0`：千張大戶持股比例增加，代表籌碼更加穩定。
- 連續多期 `thousand_lot_holder_ratio_delta_pp > 0`：代表籌碼愈加穩定，可標記為 `chip_stability = "strengthening"` 或同等語意。
- `thousand_lot_holder_ratio_delta_pp < 0`：代表籌碼穩定性轉弱或集中度下降，但不得單獨解讀為看空、出場或風險升高。
- 缺少上一期、`missing` 或 `stale` 時，不產生籌碼穩定性強化判斷。

實作邊界：

- 新分析邏輯應優先使用 TDCC v2 欄位 `thousand_lot_holder_ratio_delta_pp`，不得再用 legacy `major_holder_ratio_delta_pct` 假裝千張大戶變化。
- 若既有分析敘事仍顯示「大戶持股比例增加/下降」，需改成更精準的「千張大戶持股比例增加/下降」或「400 張以上持股比例增加/下降」。
- 若要判定「連續增加」，需基於多期有效 `shared_background_contexts` snapshot 的 period-over-period delta，不得從單一期資料推論。
- 分析輸出應使用狀態型文字，例如「千張大戶持股比例連續增加，籌碼穩定性提升」，避免輸出直接交易建議。
- `chip_stability_context` 只提供 `state`、`trend`、`summary`、`caveats` 等解釋欄位，不提供直接分數。
- 若未來要讓千張大戶穩定性影響 portfolio risk、observation confidence 或 Daily Radar ranking，需先做 production replay / forward validation，確認它與後續走勢、回撤或持倉風險有穩定關係，再另行升級規格。

### 6. Production rerun

程式修正後，需補一次 production rerun：

- 指定全體 active holdings、全體 watchlist 與 latest Daily Radar candidates 合併去重後的 symbols。
- `context_types = ["weekly_major_holders"]`。
- 驗證 `2330.TW` 等持股是否產生 fresh 或明確 missing 的 TDCC context。

Production rerun 不放在 Alembic migration 裡。Migration 只做既有 payload backfill；重新抓目前持股 symbols 需透過 internal chip-context update endpoint 執行，避免 migration 在部署時依賴外部 API。

驗證至少需檢查：

- `shared_background_contexts.context_type = 'weekly_major_holders'`
- `payload.thousand_lot_holder_ratio`
- `payload.large_holder_400_lot_plus_ratio`
- `payload.retail_100_lot_or_less_ratio`
- `payload.thousand_lot_holder_ratio_delta_pp`
- 分析或 portfolio projection 是否能呈現千張大戶增減對籌碼穩定性的判讀
- `chip_stability_context.state`
- `chip_stability_context.trend`
- `chip_stability_context.summary`
- `chip_stability_context.caveats`
- copy-to-AI 是否標注為「TDCC 週頻籌碼穩定性補充」
- technical score 與 Daily Radar ranking 是否未受 `chip_stability_context` 影響
- `freshness`
- `missing_reason`
- `/portfolio/risk-summary` 是否有對應 projection。

### 7. 完成後文件收尾

此檔屬於暫時計畫文件，不是長期 canonical spec。實作完成且 production 驗證通過後，需執行文件收尾：

- 刪除 `docs/plans/2026-06-23-tdcc-weekly-holders-scope-plan.md`。
- 將穩定後的 API contract、shared context 欄位與 migration/backfill 邊界更新到 `docs/specs/backend-api-technical-spec.md`。
- 將 TDCC provider、weekly background context 更新範圍與 market-only shared cache 邊界更新到 `docs/specs/ai-stock-sentinel-architecture-spec.md` 或 `docs/specs/daily-stock-radar-spec.md`。
- 若 frontend 顯示或 parser contract 有變更，更新 `docs/specs/frontend-architecture-spec.md`。
- 不要把此暫時計畫加入 `docs/specs/README.md`；只有完成後回寫的 canonical specs 需要維持索引一致。
