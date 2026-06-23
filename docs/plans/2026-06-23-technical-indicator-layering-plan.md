# Technical Indicator Layering Optimization Plan

## 背景

目前系統已將技術指標用在多個產品面：

1. `/analyze` 與 `/analyze/position` 的 deterministic rule-based scoring、strategy/risk language 與 LLM `signal_summary`。
2. Daily Radar 的 prepared raw data、prefilter、scoring、matched rules 與 candidate trace。
3. Analyze / Watchlist quick lookup 的前端技術指標卡片。
4. `frontend/src/lib/technicalIndicators.ts` 的「複製指標」功能，供使用者貼給外部 AI agent 做交叉分析。

目前問題不是指標數量本身，而是多個高度相關的指標會在 scoring 中重複計票。例如 MA 結構、BIAS、RSI、布林、MACD、KD、MFI、Donchian 都可能反映同一個價格強弱或過熱狀態，若每個都平權加分，會讓技術面分數被放大。

同時，Analyze 與 Daily Radar 目前不是完全共用同一套技術指標計算。Analyze 使用 `analysis.metrics`，Daily Radar 在 `daily_radar/raw_data.py` 內有自己的簡化 RSI / MFI / MACD / KD / ATR / OBV 實作，可能造成不同頁面對同一 symbol 的技術語意不一致。

## 目標

建立 technical indicator layering v1，讓後端 scoring 使用分層後的 canonical profile，而不是直接把所有 raw 指標平權納入計算。

目標結果：

- 保留既有 `technical_indicators` raw API 欄位，避免破壞前端與既有 consumer。
- 新增或投影 `technical_profile` 作為後端 scoring、LLM input summary、前端摘要與複製文字的主要語意來源。
- 統一 Analyze 與 Daily Radar 的技術指標公式來源。
- 降低重複計票風險，讓同一類訊號只在同一個 bucket 內貢獻有限分數。
- 前端仍可顯示與複製完整 raw 指標，但要清楚標示哪些是主要判斷、哪些是風險濾網、哪些只是輔助或 display-only。
- 若畫面或複製文字同時呈現 TDCC 千張大戶資訊，必須標注為「籌碼穩定性」 companion signal，不得混入 technical score。

## 非目標

此計劃不處理以下事項：

- 不刪除既有 `technical_indicators` 欄位。
- 不把外部 AI 的分析結果回寫系統。
- 不新增即時昂貴資料源。
- 不與 TDCC weekly holders、Phase 1 AVWAP 或 portfolio risk-summary 的未完成改動混在同一個實作 PR；本計劃只定義技術分層與籌碼穩定性 companion signal 的顯示 / copy 邊界。
- 不把所有 raw 指標都從 UI 移除；此計劃是「分層與權重治理」，不是「指標刪減」。

## 分層定義與分數規格

### 1. Primary score inputs

只放真正會進主要 scoring 的核心訊號：

- `ma_structure`：MA5 / MA20 / MA60 結構與 close 相對位置。
- `support_resistance`：20 日 / 60 日高低、支撐壓力、是否貼近或跌破關鍵區。
- `volume_ratio`：成交量相對 20 日均量。
- `atr_risk`：波動是否可控、支撐距離是否合理。
- `macd_momentum`：MACD histogram / momentum state。
- `obv_trend`：OBV 20 日與中長期趨勢。

Primary bucket score cap：

- 每個 primary signal 的 `impact` 只能落在 `-2` 到 `+2`。
- `primary_score = sum(primary impacts)` 後限制在 `-3` 到 `+3`。
- Primary bucket 是唯一允許正向主導 technical score 的 bucket。

### 2. Risk / overheat filters

用於限制追價、提示過熱或資料風險，不應平權當作方向加分：

- `rsi_state`
- `bias_state`
- `bollinger_state`
- `atr_state`

RSI、BIAS、布林主要回答「是否過熱、偏離、貼近上下軌、是否需要等拉回」，不應在所有情境都直接加正分。

Risk / overheat bucket score cap：

- 每個 risk filter 的 `impact` 只能落在 `-2` 到 `0`。
- `risk_filter_score = sum(risk impacts)` 後限制在 `-3` 到 `0`。
- Risk filter 不得產生正分；資料顯示「未過熱」時只回 `0`，不得額外加分。

### 3. Secondary evidence only

可作為輔助解釋與 confirmation，但不得主導分數：

- `adx`
- `donchian`
- `mfi`
- `kd`

Secondary bucket score cap：

- 每個 secondary signal 的 `impact` 只能落在 `-1` 到 `+1`。
- `secondary_score = sum(secondary impacts)` 後限制在 `-1` 到 `+1`。
- 若 secondary evidence 與 primary 訊號衝突，優先產生 caveat 或降低 confidence，不得覆蓋 primary bucket。

### 4. Display only / copy only

保留給前端顯示、外部 AI 交叉檢查與人工研究，不直接進 scoring：

- `obv` absolute value。
- `donchian_upper` / `donchian_lower` / `donchian_mid`。
- `mfi` raw value。
- `kd_k` / `kd_d` raw value。

Display-only whitelist 只能包含本節列出的欄位。新增 display-only 欄位時，必須同步更新：

- `technical_profile` contract。
- `buildTechnicalIndicatorsCopyText()` copy formatter。
- 前端 type。
- backend / frontend tests。

Display-only values 永遠不進入 `score_summary`。

### Final technical score mapping

`technical_profile.score_summary` 必須使用固定映射，避免實作各自解讀：

- `primary_score` 範圍：`-3` 到 `+3`。
- `risk_filter_score` 範圍：`-3` 到 `0`。
- `secondary_score` 範圍：`-1` 到 `+1`。
- `capped_total = primary_score + risk_filter_score + secondary_score`，再限制在 `-5` 到 `+5`。
- `technical_score = round(50 + capped_total * (17 / 5))`，維持現有 `confidence_scorer` 的 33 到 67 技術分區間。
- Display-only 欄位不得影響 `capped_total`。

若後續需要調整 cap 或映射公式，必須升級 `technical_profile.version`，並更新 backend API spec 與測試 fixture。

### Companion signal：TDCC 千張大戶籌碼穩定性

TDCC `weekly_major_holders` 不屬於技術指標，不得放入 `technical_profile.primary_score_inputs`、`risk_overheat_filters`、`secondary_evidence`、`display_only` 或 `score_summary`。若 Analyze、Watchlist quick lookup、Daily Radar modal 或複製文字需要一起呈現，應作為 `chip_stability_context` 或同等命名的 companion signal，與 technical score 分離。

建議語意：

```json
{
  "chip_stability_context": {
    "source": "tdcc_weekly_major_holders",
    "status": "fresh",
    "as_of_date": "2026-06-18",
    "thousand_lot_holder_ratio": 38.2,
    "thousand_lot_holder_ratio_delta_pp": 1.52,
    "trend": "strengthening",
    "summary": "千張大戶持股比例增加，籌碼穩定性提升"
  }
}
```

判斷規則：

- `thousand_lot_holder_ratio_delta_pp > 0`：千張大戶持股比例增加，代表籌碼更加穩定。
- 連續多期 `thousand_lot_holder_ratio_delta_pp > 0`：代表籌碼愈加穩定，`trend` 可標記為 `strengthening`。
- `thousand_lot_holder_ratio_delta_pp < 0`：代表籌碼穩定性轉弱或集中度下降，但不得單獨解讀為看空、出場或風險升高。
- `missing`、`stale` 或沒有有效上一期時，不產生籌碼穩定性強化判斷。
- 不得使用 legacy `major_holder_ratio_delta_pct` 假裝千張大戶變化；若同時顯示 `large_holder_400_lot_plus_ratio_delta_pp`，文案必須明確標成「400 張以上」而非「千張大戶」。

Scoring 邊界：

- `chip_stability_context` 不影響 `technical_profile.score_summary.capped_total` 或 `technical_score`。
- 若後續要讓千張大戶穩定性影響 portfolio risk 或 observation confidence，應在 chip / portfolio risk layer 另行建模，不在 technical layer 內加權。
- LLM input 可以把它放在「籌碼穩定性補充」段落，避免和「技術指標摘要」混在同一 bucket。

## API 與資料契約

### `technical_indicators`

保留既有欄位與語意，短期不做破壞性刪除：

- MA / high-low / Bollinger / MACD / KD / ADX / OBV / ATR / MFI / Donchian raw values 與 label。
- 資料不足時維持 `null`，主流程不失敗。

### `technical_profile`

新增 canonical profile，建議 shape：

```json
{
  "version": "technical-layer-v1",
  "primary_score_inputs": {
    "ma_structure": {
      "state": "bullish_alignment",
      "impact": 2,
      "reason": "close > MA5 > MA20"
    },
    "support_resistance": {
      "state": "near_support",
      "impact": 1,
      "reason": "close is within 2% of MA20/support zone"
    },
    "volume_ratio": {
      "state": "constructive_participation",
      "impact": 1,
      "value": 1.28
    },
    "atr_risk": {
      "state": "contained",
      "impact": 0
    },
    "macd_momentum": {
      "state": "positive_histogram",
      "impact": 1
    },
    "obv_trend": {
      "state": "rising",
      "impact": 1
    }
  },
  "risk_overheat_filters": {
    "rsi_state": {
      "state": "overheated",
      "impact": -1,
      "value": 73.2
    },
    "bias_state": {
      "state": "extended",
      "impact": -1,
      "value": 11.4
    },
    "bollinger_state": {
      "state": "near_upper",
      "impact": -1
    },
    "atr_state": {
      "state": "medium",
      "impact": 0
    }
  },
  "secondary_evidence": {
    "adx": {
      "state": "strong_bullish_trend",
      "impact": 1
    },
    "donchian": {
      "state": "near_upper",
      "impact": 0
    },
    "mfi": {
      "state": "bullish_flow",
      "impact": 0
    },
    "kd": {
      "state": "neutral",
      "impact": 0
    }
  },
  "display_only": {
    "obv_absolute_value": 42850000,
    "donchian_upper": 950.0,
    "donchian_lower": 900.0,
    "mfi": 62.1,
    "kd_k": 55.2,
    "kd_d": 49.8
  },
  "score_summary": {
    "primary_score": 3,
    "risk_filter_score": -2,
    "secondary_score": 1,
    "capped_total": 2,
    "technical_score": 57
  },
  "data_quality": {
    "data_date": "2026-06-23",
    "is_final": true,
    "lookback_days_available": 120,
    "required_lookback_days": 60,
    "ohlcv_aligned": true,
    "volume_aligned": true,
    "missing_fields": []
  },
  "formula_versions": {
    "metrics": "technical-metrics-v1",
    "layering": "technical-layer-v1"
  },
  "companion_context_refs": {
    "chip_stability_context": "tdcc_weekly_major_holders"
  },
  "caveats": [
    "RSI/BIAS/Bollinger are treated as risk filters, not independent bullish evidence.",
    "KD/MFI/Donchian are secondary evidence only.",
    "TDCC thousand-lot holder changes are chip-stability companion signals, not technical score inputs."
  ]
}
```

## 後端實作計劃

### 1. 建立 canonical technical profile builder

目標檔案：

- `backend/src/ai_stock_sentinel/technical/profile.py`
- `backend/src/ai_stock_sentinel/technical/metrics.py`（若需要從 `analysis.metrics` 搬出 pure metrics）

職責：

- 接收 OHLCV arrays / snapshot。
- 呼叫同一套 canonical metrics 計算。
- 回傳 `technical_indicators` raw values。
- 回傳 `technical_profile` layer semantics。
- 負責 score bucket cap 與 caveat 產生。

模組邊界：

- `ai_stock_sentinel.technical` 必須是 pure domain module。
- 不得依賴 `analysis.schemas`、`analysis.router`、`graph`、FastAPI route、LLM prompt、Daily Radar repository 或 frontend contract。
- `analysis` 與 `daily_radar` 都只能呼叫此 pure module，不得讓 `daily_radar` 反向依賴 analysis feature module。
- 若保留 `analysis.metrics` 作為相容 wrapper，也只能 re-export pure metrics，不得保留兩套公式。

### 2. 收斂 Analyze technical 計算入口

目標檔案：

- `backend/src/ai_stock_sentinel/analysis/application/response_builder.py`
- `backend/src/ai_stock_sentinel/analysis/schemas.py`
- `backend/src/ai_stock_sentinel/graph/nodes.py`

調整：

- `/analyze` 與 `/analyze/position` response 新增 `technical_profile`。
- LLM `signal_summary` 優先使用 `technical_profile`，raw 指標只作補充。
- `technical_indicators` 保持向後相容。
- `technical_profile.data_quality` 必須包含 `data_date`、`is_final`、lookback coverage、OHLCV 對齊狀態、volume 對齊狀態與 missing fields。
- `technical_profile.formula_versions` 必須能追蹤 metrics 與 layering 的版本。
- 若 response 同時帶 `chip_stability_context`，LLM `signal_summary` 應將其放在籌碼穩定性補充，不得把千張大戶增加寫成技術面加分。

### 3. 改 scoring 與 strategy 權重

目標檔案：

- `backend/src/ai_stock_sentinel/analysis/confidence_scorer.py`
- `backend/src/ai_stock_sentinel/analysis/strategy_generator.py`

調整：

- 不再讓 RSI / BIAS / Bollinger / KD / MFI / Donchian 平權散落加分。
- 改用 `technical_profile.score_summary` 或 layer impact。
- Primary / risk / secondary bucket 必須使用本文件定義的固定 cap 與 technical score mapping。
- Display-only 不進分。

### 4. 統一 Daily Radar 技術計算

目標檔案：

- `backend/src/ai_stock_sentinel/daily_radar/raw_data.py`
- `backend/src/ai_stock_sentinel/daily_radar/scoring.py`
- `backend/src/ai_stock_sentinel/daily_radar/universe.py`
- `backend/src/ai_stock_sentinel/daily_radar/prefilter.py`

調整：

- 移除或降級 `daily_radar/raw_data.py` 內自有簡化 RSI / MFI / MACD / KD / ATR / OBV 實作。
- Daily Radar prepared raw data 仍保留舊 `indicators` key，避免破壞 candidate trace。
- 新增 `technical_profile` 或等價 projection 到 `input_snapshot`。
- Scoring 逐步改用 layer semantics，而不是直接拿每個 raw 指標加分。
- `score_breakdown` 必須能回放 layer impact、bucket cap 前後分數、`technical_profile.version`、`formula_versions` 與 `data_quality`。

Daily Radar 排名變動驗證：

- 必須選一批 production-like prepared run fixture 或歷史 prepared run replay。
- 比較變更前後 selected candidates、bucket scores、matched rules、risk penalties 與 observation score。
- 驗證報告需列出排序變動最大的樣本，以及人工判斷是否符合分層語意。
- 若 selected candidates 大量翻轉，必須先調整 cap / mapping 或補 calibration note，不能只用 unit tests 放行。

### 5. 前端 type 與顯示

目標檔案：

- `frontend/src/lib/analysisTypes.ts`
- `frontend/src/pages/AnalyzePage.tsx`
- `frontend/src/pages/WatchlistPage.tsx`
- `frontend/src/lib/technicalIndicators.ts`

調整：

- 新增 `TechnicalProfile` type。
- 技術指標卡片改成分層顯示：
  - 主要判斷
  - 風險 / 過熱濾網
  - 輔助證據
  - 完整 raw 指標
- 若頁面顯示 `chip_stability_context`，需獨立放在籌碼穩定性區塊，文字使用「千張大戶持股比例增加，籌碼穩定性提升」這類狀態型文案，不放進技術指標分數卡。
- Watchlist quick lookup 使用同一套 formatter，不另外 fork copy 邏輯。
- 缺少 `technical_profile` 的舊 response 或舊 cache，只能顯示 legacy raw 技術指標卡，不顯示分層結論。
- 若 `technical_profile.data_quality` 顯示資料不足或盤中資料，前端分層摘要需顯示對應 caveat，不能當成完整收盤判斷。

### 6. 複製文字升級

`buildTechnicalIndicatorsCopyText()` 需改成 AI-friendly format：

```text
技術指標摘要
股票名稱：
股票代碼：
資料狀態：收盤 / 盤中
用途說明：以下分層為系統判斷權重，raw 指標供外部 AI 交叉檢查，不代表全部都等權進入系統 scoring。

[Primary score inputs]
...

[Risk / overheat filters]
...

[Secondary evidence]
...

[Display-only raw values]
...

[Chip stability companion]
千張大戶持股比例：
千張大戶持股比例變化：
籌碼穩定性狀態：
說明：此段為 TDCC 週頻籌碼穩定性補充，不納入 technical score。

[Data caveats]
...
```

複製文字必須同時滿足：

- 足夠完整，讓使用者不用再次打 API 就能請外部 AI 分析。
- 足夠明確，避免外部 AI 將 display-only 指標誤解為系統主要判斷。
- 保留 `phase1_observation` / AVWAP 相關 rows，但需維持其 non-blocking evidence 語意。
- 若包含 TDCC 千張大戶資訊，需清楚寫明這是「籌碼穩定性」 companion signal；千張大戶增加代表籌碼更加穩定，連續增加代表籌碼愈加穩定，但不等於直接買賣建議。

## 測試計劃

Backend tests：

- canonical profile builder 可產生四層分組。
- score cap 與 `technical_score = round(50 + capped_total * (17 / 5))` 映射固定且可測。
- RSI / BIAS / Bollinger 只進 risk filter，不作一般正向加分。
- KD / MFI / Donchian 只進 secondary 或 display-only，不主導 primary score。
- Display-only values 不影響 score。
- `chip_stability_context` 不影響 `technical_profile.score_summary`，且不得進入 primary / risk / secondary / display-only bucket。
- 千張大戶增減只讀 `thousand_lot_holder_ratio_delta_pp`；測試需防止用 legacy `major_holder_ratio_delta_pct` 假裝千張大戶變化。
- 新增 display-only 欄位時，未更新 contract/copy/type/tests 會失敗。
- 資料不足時 raw 欄位為 `null`，profile caveats 明確，主流程不失敗。
- Daily Radar 與 Analyze 使用同一套 metrics 公式，至少針對固定 OHLCV fixture 驗證 MACD / KD / ATR / MFI / OBV 結果一致。
- Daily Radar scoring 的排名變動需可 trace：score breakdown 中應看得到 layer impact。
- Daily Radar production-like replay 需比較 selected candidates、bucket score、matched rules 與排序變動。

Frontend tests：

- `TechnicalProfile` type 與 API parser 相容。
- Analyze 技術指標卡片能顯示分層摘要與 raw 區塊。
- 缺少 `technical_profile` 時 UI fallback 到 legacy raw 指標卡，不顯示分層結論。
- Watchlist quick lookup 仍能複製完整技術指標。
- `buildTechnicalIndicatorsCopyText()` 包含四層分組、raw values、data caveats、資料狀態與 symbol/name。
- 若 payload 包含 `chip_stability_context`，copy 文字需包含獨立 `[Chip stability companion]` 區塊，並標明不納入 technical score。
- 缺少 `technical_profile` 的舊 response 仍 fallback 到舊 `technical_indicators` copy。

## 驗收標準

完成後至少需確認：

- `/analyze` 舊 `technical_indicators` 欄位仍存在。
- `/analyze` 新增 `technical_profile`。
- `skip_ai: true` 回傳足以支援 Watchlist quick lookup 與複製文字。
- Daily Radar 不再依賴簡化 KD/MFI/MACD/ATR 實作作為主要 scoring 來源。
- Scoring 不會因高度相關指標重複加分而過度偏多。
- `technical_profile` 具有明確 `version`、`data_quality` 與 `formula_versions`。
- TDCC 千張大戶持股比例增減若被呈現，需獨立於 technical score，並標注為籌碼穩定性：增加代表籌碼更加穩定，連續增加代表籌碼愈加穩定。
- Daily Radar replay 驗證已說明排名變動與可接受原因。
- 前端複製文字可直接貼給外部 AI agent，並清楚說明 raw 指標與 scoring 權重的差異。

## Release 與文件收尾

此檔是臨時計劃文件，不是 canonical spec。實作完成且驗證通過後：

- 刪除 `docs/plans/2026-06-23-technical-indicator-layering-plan.md`。
- 更新 `docs/specs/backend-api-technical-spec.md`，記錄 `technical_profile` API contract、layer semantics、相容策略。
- 更新 `docs/specs/daily-stock-radar-spec.md`，記錄 Daily Radar 使用 canonical technical profile 與 scoring layer impact。
- 更新 `docs/specs/frontend-architecture-spec.md`，記錄前端技術指標分層顯示、`chip_stability_context` companion 區塊與 copy contract。
- 不把此臨時計劃加入 `docs/specs/README.md`；只有完成後回寫的 canonical specs 需要維持索引一致。
