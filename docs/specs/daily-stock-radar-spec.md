# Daily Stock Radar MVP 規格

> 類型：長期產品介面規格
> 日期：2026-06-11
> 狀態：Draft v0.1
> 定位：每日收盤後產生隔日觀察清單，協助使用者找到值得追蹤的技術與籌碼 setup，而不是給出交易建議。

---

## 1. 為何需要獨立規格

`docs/specs/README.md` 原則上避免新增零散 spec。Daily Stock Radar 仍應獨立成檔，原因是它不是單一階段需求，也不是 `/analyze` 的小改版，而是一個新的長期產品表面。

| 面向 | 獨立成檔理由 |
| ---- | ------------ |
| 產品語意 | 從「單股分析」擴展為「每日市場雷達」，需要自己的輸出語言與使用情境 |
| 評分模型 | 需要 bucket 分數、內部排序分、風險扣分與可追溯規則 |
| 持久化 | 需要保存每日候選清單、歷史排名、冷卻狀態與解釋依據 |
| API | 需要批次掃描、查詢清單、查詢單股雷達紀錄等端點 |
| UI | 需要雷達頁、bucket 分類、風險標籤、重複出現提示與細節抽屜 |
| 排程與運維 | 需要 GitHub Actions 於收盤後呼叫 Zeabur 內部端點 |

本文件是 Daily Stock Radar MVP 的單一事實來源。若後續 API 或資料庫欄位落地，相關契約可再同步到 `backend-api-technical-spec.md`。

---

## 2. 產品方向與語言邊界

### 2.1 核心方向

Daily Radar 的任務是找出「明天值得放進觀察清單的 setup」，不是推薦交易。

使用者看到的結果應回答：

1. 今天收盤後，哪些股票出現值得觀察的結構。
2. 每檔股票屬於哪一種 setup bucket。
3. 為什麼它被選出，資料證據是什麼。
4. 有哪些風險標籤，需要先觀察而不是追價。

### 2.2 文字護欄

所有前端文案、API 欄位與說明文字必須使用觀察語言。

| 不使用 | 改用 |
| ------ | ---- |
| 買進、賣出、加碼、出場建議 | 觀察、追蹤、留意、風險升高 |
| 必買、可進場、目標價 | 候選、setup、觀察價位、壓力區 |
| 強烈推薦 | 高分觀察、訊號集中 |

資料欄位可描述法人買賣超、融資增減等市場事實，但不得把結果包裝成交易命令。

### 2.3 與週頻大戶持股雷達的切分

Daily Radar 不使用週頻大戶持股比例當作每日訊號。`TaiwanStockHoldingSharesPer` 與 stockholder distribution 屬於週更新資料，適合獨立的 weekly big-holder/shareholding radar。

Daily Radar 只處理日頻可穩定更新的資料。週頻資料可在未來的週報或中期趨勢頁呈現，不混入每日排名，以免造成資料時效錯覺。

---

## 3. MVP 資料範圍

### 3.1 納入資料

| 類別 | MVP 欄位或來源 | 用途 |
| ---- | -------------- | ---- |
| OHLCV | yfinance 日線開高低收量 | 趨勢、波動、量價結構、均線與支撐壓力 |
| 技術指標 | MA、EMA、RSI、MACD、KD、ATR、OBV、MFI、布林通道、Donchian | bucket 訊號與風險扣分 |
| 法人買賣超 | TWSE RWD fund reports（`TWT38U` / `TWT44U`） | 外資、投信方向與連續性；以 report-level source trace 保存來源 |
| 融資融券 | Minimal margin summary / future full margin context | 散戶追價、融資風險、軋空或籌碼壓力觀察；目前 live run 不宣稱完整融資融券已取得 |
| 大盤指數 | 加權指數或等價 market index OHLCV | 市場風險濾網與相對強弱基準 |

### 3.2 明確延後

| 延後項目 | 延後理由 |
| -------- | -------- |
| 借券資料 | MVP 先控制資料源與解釋複雜度，後續可作空方壓力補充 |
| 週頻大戶與股權分散資料 | 更新頻率不適合每日雷達，另屬 weekly radar |
| 完整產業相對強弱 | 需要穩定產業分類與成分股維護，MVP 只做大盤相對強弱 |
| AI/LLM 選股 | MVP 必須可重現、可測試、可回放，選股由 deterministic rule-based pipeline 完成 |

本 MVP 不呼叫 LLM 進行股票選擇、排名或 bucket 判定。若未來加入 LLM，只能用於解釋文字潤飾，且不得覆寫規則結果。

---

## 4. 掃描範圍與兩階段流程

### 4.1 掃描標的範圍

目前 live run 不從全市場靜態清單開始逐檔掃描，而是由後端自行建立 multi-track universe 後再進入資料補齊與評分。Universe 來源如下，最後以軌道優先序保留、去重後形成當日 selected symbols：

1. Same-day institutional leaders：讀取當日外資與投信正買超名單，先為每個 symbol 選出分數較佳的法人角色，再將合併去重後的名單排序，最後取 combined top 50。
2. Recent accumulation/concentration leaders：讀取最近 5 個交易日的累積與集中度排名 top 50。
3. Daily trigger technical tracks：從當日已存在的 final `StockRawData` OHLCV/technical indicators 建立 `price_volume`、`reversal`、`support_retake` 等日頻 trigger tracks，不對全市場逐檔呼叫外部 API。
4. Final selected universe：依上述軌道順序合併，保留第一次出現的位置，重複 symbol 只留一筆，並保存 `primary_track`、`tracks` 與 `track_metrics` trace。

這代表 live run 的 universe 來自法人雙軌加上本地日頻技術 trigger tracks，實際數量會因軌道重疊去重而低於各軌 limit 加總。MVP 原始設計仍排除 ETF、權證、特別股與資料欄位明顯不完整標的，但目前 live default 是 multi-track 候選 universe，不是完整上市櫃全市場逐檔掃描。

### 4.2 兩階段掃描

為控制外部請求量，Daily Radar 採兩階段流程。

| 階段 | 目的 | 動作 | 輸出 |
| ---- | ---- | ---- | ---- |
| Stage 1 broad cheap prefilter | 用低成本資料縮小候選池 | 讀取已快取 OHLCV、基本流動性、價格、近況結構 | 約 Top 100 或 Top 200 候選 |
| Stage 2 detailed scoring | 對候選做完整評分 | 補齊法人、融資、技術指標與大盤相對位置 | 每日雷達清單與解釋 |

原則：Stage 1 不應對全市場逐檔打昂貴外部 API。GitHub Actions 只呼叫 `POST /internal/daily-radar/run`，資料載入、必要補齊、Stage 1 prefilter 與 Stage 2 detailed scoring 都由 Daily Radar service 內部負責。若未來需要拆分預取，另建 `POST /internal/daily-radar/prefetch`，不得直接重用 `/internal/fetch-raw-data`。

目前已落地的 live run 是自包含流程：`POST /internal/daily-radar/run` 先呼叫 multi-track universe 選股，再對 selected symbols 補齊缺少的 OHLCV，接著執行 Stage 1/2 rule-based scoring，最後寫入 run log 與 candidates。公開 Daily Radar 讀取端點與 response schema 不因這個後端流程改變。

### 4.3 外部資料 request budget

| 資料源 | 目前 live 規則 |
| ------ | -------------- |
| TWSE RWD institutional reports | 目前 live provider 讀取 `TWT38U` 與 `TWT44U` fund reports 建立 same-day institutional 與 recent accumulation universe。這是 report-level all-report 查詢，不是 selected symbols 的逐檔法人 request。 |
| yfinance selected-symbol OHLCV | 只對 selected universe 中缺少 final `StockRawData` 的 symbol 做一次 batch download。Batch 以 `run_date - 120 days` 到 `run_date + 1 day` 取日線，再只保留 `run_date` 當日或之前的資料。已有 final raw rows 會重用，不重抓。 |
| yfinance market index OHLCV | 每次 run 只抓固定 market benchmark：TW 使用 `TAIEX` / `^TWII`，US 使用 `SPX` / `^GSPC`。用於 market regime 與 relative strength benchmark，不做全市場逐檔抓取。 |
| Shared background context cache | Daily run 只批次讀取 `shared_background_contexts` 中 selected symbols 的背景 context，不在主流程即時呼叫 weekly major holders、lending 或 full margin provider。背景更新由獨立 GitHub Actions 呼叫 internal endpoint 完成。 |

目前 live 限制：尚未做完整 live margin fetch。為避免技術與法人資料被誤判為 stale，回填的 raw rows 只放入最小 margin `data_date`，不宣稱已取得完整融資融券內容。Market context 已接上固定大盤指數來源並產生 basic regime；更完整的大盤濾網仍屬後續補強。Phase 2B 已將 weekly major holders、lending 與 full margin context 以 background labels/detail trace 接到 Daily Radar surface，但這些背景脈絡不是 ranking driver。Phase 2C/2D 已讓 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 以 read/reference 方式使用 shared context；它只作 evidence、caveat 與資料品質 trace，不覆寫 deterministic action、portfolio action、verdict、classification 或 point-in-time lifecycle replay。

---

## 5. 嚴格前置濾網

所有標的必須通過前置濾網才可進入排名。濾網是 hard gate，不用分數補償。

| 濾網 | MVP 規則方向 | 淘汰原因 |
| ---- | ------------ | -------- |
| 流動性 | 近 20 日平均成交金額需達最低門檻 | 避免低流動性造成滑價與假訊號 |
| 最低股價 | 收盤價需高於最低價格門檻 | 避免低價股極端波動扭曲評分 |
| 資料完整度 | 近 60 個交易日 OHLCV 不可缺漏過多，法人與融資資料需有可用日期 | 避免用缺資料排名 |
| 過度延伸 | 短期漲幅、乖離率、RSI、布林位置超過門檻時排除或降級 | 避免把追高型態列為高分 setup |
| 弱勢結構 | 收盤價長期低於主要均線且低點下移 | 避免下跌趨勢中的反彈雜訊 |
| 融資風險 | 融資快速增加且價格未同步轉強 | 避免散戶擁擠風險 |
| 資料時效 | 最新資料日期落後最近交易日超過容忍值 | 避免 stale data 進入雷達 |

前置濾網需回傳 `prefilter_status` 與 `prefilter_reasons`，方便除錯與前端顯示「未入選原因」。

---

## 6. 四個 setup buckets

每檔入選股票必須有一個 primary bucket，也可有 secondary bucket。bucket 只描述觀察型態，不代表交易方向。

| Bucket | 中文名稱 | 核心訊號 | 適合文案 |
| ------ | -------- | -------- | -------- |
| `institutional_accumulation` | 法人與主力累積 | 法人連續買超、投信或外資方向一致、價格尚未過熱 | 籌碼集中觀察 |
| `price_volume_strengthening` | 量價轉強 | 放量突破整理、OBV 或 MFI 轉強、收盤站回關鍵均線 | 量價同步觀察 |
| `bottoming_reversal` | 底部反轉 | 跌勢收斂、低檔量縮後轉強、MACD 或 KD 翻正 | 低位轉折觀察 |
| `support_retest` | 支撐回測 | 回測 MA20、MA60、前高或區間支撐後止跌 | 支撐有效性觀察 |

Bucket 判定需可重現。同一標的若同時命中多個 bucket，primary bucket 取分數最高者，secondary bucket 保留在解釋欄位。

---

## 7. 評分模型

### 7.1 Bucket specific scores

每個 bucket 各自產生 0 到 100 分，使用該 bucket 專屬規則。

| Bucket | 加分項 | 扣分項 |
| ------ | ------ | ------ |
| 法人與主力累積 | 連續法人買超、投信方向穩定、價格未大幅脫離均線 | 法人買超但收黑、融資同步暴增、短線過熱 |
| 量價轉強 | 成交量高於均量、收盤突破整理區、OBV 同步上升 | 爆量留上影、突破後跌回區間、RSI 過熱 |
| 底部反轉 | 長期跌幅後低點不再破、MACD 柱狀體改善、KD 低位翻正 | 均線仍空頭發散、法人持續賣超、量能不足 |
| 支撐回測 | 接近支撐後收復、ATR 風險可控、量縮整理 | 跌破支撐、融資增加、反彈量價不足 |

### 7.2 Composite observation score

`observation_score` 為每日排序主分數，範圍 0 到 100。

建議組成：

| 組成 | 權重方向 |
| ---- | -------- |
| Primary bucket score | 主要來源 |
| Cross confirmation | 法人、量價、技術至少兩項同向時加分 |
| Market context | 大盤站上關鍵均線且波動未擴大時加分 |
| Freshness | 初次出現或冷卻後再出現時加分 |
| Relative strength | 候選與 market benchmark 在可對齊交易日上的相對報酬差，資料不足時只保存 missing reason，不補中性值 |

分數用途是內部排序、降級、回測校準與 traceability，不是承諾結果。前端預設應以觀察等級、bucket、風險標籤與命中原因呈現；`observation_score` 可保留在 API 與 advanced trace，但不應作為一般使用者主畫面的 headline，也不得稱為勝率或推薦分數。

### 7.3 Risk penalties

風險扣分獨立計算，且需保留原因。

| 風險 | 扣分方向 | 風險標籤 |
| ---- | -------- | -------- |
| 過熱 | 乖離率過高、RSI 高檔、連續急漲 | `overextended` |
| 籌碼分歧 | 法人買賣方向不一致或由買轉賣 | `flow_conflict` |
| 融資擁擠 | 融資快速增加且價格未確認 | `margin_crowding` |
| 大盤風險 | 指數跌破關鍵均線或波動放大 | `market_weakness` |
| 資料不足 | 任一核心資料源缺漏 | `data_gap` |

### 7.4 Traceability

每個入選結果必須保存可追溯證據。

最小欄位：

| 欄位 | 說明 |
| ---- | ---- |
| `matched_rules` | 命中的規則代碼與中文說明 |
| `score_breakdown` | bucket 分、cross confirmation、market context、relative strength、freshness、risk penalties、內部排序分、`scoring_version` 與 `rule_version`；作為 advanced trace / debug evidence，不是預設主畫面 |
| `input_snapshot` | OHLCV、法人、融資、技術指標、大盤狀態、relative strength trace、版本資訊與 replayable evidence |
| `data_dates` | 各資料源最新日期 |
| `prefilter_reasons` | 通過或排除原因 |

已落地的 replayable evidence 使用 consumer-neutral shape，至少包含 `evidence_type`、`source`、`as_of_date`、`freshness`、`missing_reason`、`replay_key` 與 `applicable_consumers`。Phase 1 先接 Daily Radar；Phase 2C/2D 已讓其他 consumers 以 read/reference 方式引用，不得覆寫 deterministic action、score、verdict、classification 或 lifecycle replay。

Phase 2A 已新增 shared background context cache foundation。`shared_background_contexts` 保存 `symbol`、`context_type`、`applicable_consumers`、`source`、`as_of_date`、`freshness`、`payload`、`missing_reason` 與 `replay_key`。同一 `symbol` / `context_type` / `replay_key` 可 upsert 更新；不同 `replay_key` 會保留歷史 trace，讓 point-in-time consumer 可回放 `as_of_date <= reference_date` 的最近 context。Phase 2B 起，Daily Radar 是第一個 label/detail consumer：selected symbols 會批次讀 cache，將 raw trace 放入 `input_snapshot.background_context`，並以 `background_context_labels` 顯示大戶持股集中背景、借券空方壓力背景與完整融資融券背景。Missing/stale context 必須保留 freshness 與 missing reason；背景 labels 不改 candidate ranking、bucket、risk labels 或 `observation_score`。Phase 2C/2D 起，`/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review 也讀取相同 shared context vocabulary；read path 會尊重 `applicable_consumers`，lifecycle review 以事件日期做 point-in-time filter，遇到沒有可用歷史 context 且只有未來 context 時只能保留 missing/future-excluded caveat。

### 7.5 Relative strength 與 calibration

Relative strength 由 Python deterministic code 計算，不由 LLM 或文字推估。預設 lookback 為 20 個可對齊交易日，公式為：

```text
candidate_return = candidate_end_close / candidate_start_close - 1
benchmark_return = benchmark_end_close / benchmark_start_close - 1
relative_value = candidate_return - benchmark_return
```

資料不足、benchmark stale 或候選與 benchmark 無法對齊時，`score_breakdown.relative_strength` 保留 `freshness` 與 `missing_reason`，`relative_value` 為 `null`，不假裝成中性訊號。

Calibration workflow 已提供可重跑 JSON report：

```bash
cd backend
uv run python scripts/daily_radar_calibration.py --source fixture --run-date 2026-05-29
```

報表包含 sample count、bucket distribution、rank cutoff impact、bucket threshold impact、risk/overheat impact、relative strength impact、skip reasons 與 version manifest。Calibration report 只作為規則校準診斷，不宣稱勝率、不輸出價格承諾，也不自動改 live scoring 行為。

---

## 8. 解釋、風險標籤與重複處理

### 8.1 Rule-based explanations

解釋文字由規則模板產生，不由 LLM 選股。每檔股票至少包含：

1. 一句 setup 摘要。
2. 三到五個證據點。
3. 一到三個風險標籤。
4. 隔日觀察重點，例如「觀察是否守住 MA20」或「觀察量能是否延續」。

範例語氣：

```text
量價轉強觀察：今日收盤站回 MA20，成交量高於 20 日均量，OBV 同步轉強。短線乖離仍在可接受範圍，隔日可觀察是否維持在突破區上方。
```

### 8.2 Cooldown 與 repeat handling

避免同一股票連續多日佔據榜單但沒有新資訊。

| 狀態 | 規則 |
| ---- | ---- |
| `new` | 近 N 個交易日未入選，今日首次命中 |
| `repeat` | 連續入選但分數與 bucket 沒有明顯變化 |
| `upgraded` | 分數提高或新增更強 bucket |
| `cooled_down` | 近期入選後訊號消退，暫不重複顯示 |

前端預設優先顯示 `new` 與 `upgraded`。`repeat` 可保留在次要區塊，並顯示「連續觀察第 X 天」。

---

## 9. 持久化模型

延續現有 FastAPI 與資料庫架構，Daily Radar 可重用 `stock_raw_data` 作為原始資料快取，並與 `daily_analysis_log` 保持關聯。

### 9.1 既有資料表關係

| 既有表 | 關係 |
| ------ | ---- |
| `stock_raw_data` | 儲存 OHLCV、法人、融資、技術指標輸入快照或來源資料 |
| `daily_analysis_log` | 可保存單股分析結果，Daily Radar 入選後可連回個股分析紀錄 |

### 9.2 MVP 新增概念表

若實作需要，建議新增 `daily_radar_runs` 與 `daily_radar_candidates`。欄位可先保持簡單。

```text
daily_radar_runs
- id
- run_date
- market
- status
- started_at
- finished_at
- universe_count
- prefilter_count
- candidate_count
- errors

daily_radar_candidates
- id
- run_id
- name
- symbol
- primary_bucket
- secondary_buckets
- observation_score
- bucket_scores
- risk_labels
- matched_rules
- explanation
- repeat_status
- scoring_version / rule_version（由 `score_breakdown` trace 浮出，舊資料可為 null）
- data_dates
- input_snapshot
- score_breakdown

shared_background_contexts
- id
- symbol
- context_type
- applicable_consumers
- source
- as_of_date
- freshness
- payload
- missing_reason
- replay_key
- created_at / updated_at
```

資料庫需支援依日期查詢榜單、依 symbol 回看歷史入選紀錄、依 bucket 查詢當日候選。

---

## 10. API surface

Daily Radar 以現有 FastAPI 為後端基礎，並與既有端點共存。

### 10.1 既有端點關係

| 端點 | 關係 |
| ---- | ---- |
| `POST /internal/fetch-raw-data` | 既有單一 symbol 原始資料刷新端點，不是 Daily Radar 排程依賴，也不被 Daily Radar workflow 重用 |
| `POST /analyze` | 使用者點進單股時可執行完整單股分析 |
| `POST /analyze/position` | 持股頁仍處理既有倉位診斷，與 Daily Radar 語意分離 |

### 10.2 MVP 新增端點草案

| Method | Path | 用途 |
| ------ | ---- | ---- |
| `POST` | `/internal/daily-radar/run` | 由 GitHub Actions 呼叫的內部掃描端點 |
| `GET` | `/daily-radar/latest` | 查詢最新雷達清單 |
| `GET` | `/daily-radar/{run_date}` | 查詢指定日期雷達清單 |
| `GET` | `/daily-radar/symbol/{symbol}` | 查詢單一股票雷達歷史 |

### 10.3 Response 欄位草案

```json
{
  "run_date": "2026-06-01",
  "status": "completed",
  "candidates": [
    {
      "symbol": "2330.TW",
      "name": "台積電",
      "primary_bucket": "price_volume_strengthening",
      "secondary_buckets": ["institutional_accumulation"],
      "observation_score": 82,
      "risk_labels": ["market_weakness"],
      "repeat_status": "new",
      "explanation": "量價轉強觀察...",
      "scoring_version": "daily-radar-scoring-v2.1c",
      "rule_version": "daily-radar-rules-v2.1c",
      "bucket_scores": {},
      "score_breakdown": {
        "relative_strength": {
          "benchmark_symbol": "TAIEX",
          "lookback_days": 20,
          "relative_value": null,
          "freshness": "missing",
          "missing_reason": "candidate_price_history_missing"
        }
      },
      "input_snapshot": {
        "market_context": {"regime": "constructive"},
        "evidence": [
          {
            "evidence_type": "relative_strength",
            "source": {
              "domain": "daily_trigger_signal",
              "provider": "deterministic_relative_strength",
              "benchmark_symbol": "TAIEX"
            },
            "as_of_date": null,
            "freshness": "missing",
            "missing_reason": "candidate_price_history_missing",
            "replay_key": "relative_strength:2330.TW:TAIEX:missing:L20",
            "applicable_consumers": ["daily_radar"],
            "details": {"lookback_days": 20}
          }
        ]
      },
      "data_dates": {},
      "matched_rules": [],
      "background_context_labels": [
        {
          "context_type": "weekly_major_holders",
          "label": "大戶持股集中背景",
          "source": {
            "domain": "background_context",
            "provider": "shared_background_context_cache"
          },
          "as_of_date": "2026-05-31",
          "freshness": "fresh",
          "missing_reason": null,
          "replay_key": "background_context:2330.TW:weekly_major_holders:2026-05-31",
          "applicable_consumers": ["daily_radar"]
        },
        {
          "context_type": "full_margin",
          "label": "完整融資融券背景資料未更新",
          "source": {
            "domain": "background_context",
            "provider": "shared_background_context_cache"
          },
          "as_of_date": null,
          "freshness": "missing",
          "missing_reason": "context_cache_missing",
          "replay_key": "background_context:2330.TW:full_margin:missing",
          "applicable_consumers": ["daily_radar"]
        }
      ]
    }
  ]
}
```

---

## 11. 前端體驗

React 前端新增 Daily Radar 頁，定位為每日觀察清單。

### 11.1 頁面結構

| 區塊 | 內容 |
| ---- | ---- |
| Header | 最新掃描日期、資料日期、掃描狀態、候選數 |
| Market context | 大盤狀態、波動與整體風險提示 |
| Bucket tabs | 四個 setup buckets 與數量 |
| Candidate list | 股票、bucket、觀察等級或優先順序、風險標籤、repeat 狀態 |
| Detail drawer | 命中規則、資料日期、隔日觀察重點、shared background context labels；分數拆解僅作為 advanced trace |
| Link out | 連到 `/analyze` 做單股完整分析 |

### 11.2 互動原則

1. 預設排序使用內部 `observation_score`，但 UI 預設呈現觀察等級與理由，不以 raw 0-100 分數作為主視覺；可按 bucket 篩選。
2. 高風險標籤需在列表直接可見，不能只藏在詳情。
3. `repeat` 標的需顯示連續天數，避免使用者誤以為每日都是新訊號。
4. 空狀態需說明是「今日沒有通過濾網的高品質 setup」，不是系統失敗。

---

## 12. 排程與運維

### 12.1 GitHub Actions 到 Zeabur

生產環境目標為 Zeabur，不假設 Render。

建議流程：

1. 台股收盤且資料源更新後，GitHub Actions 以 cron 觸發。
2. Action 只呼叫 Zeabur 後端的內部端點 `POST /internal/daily-radar/run`。
3. Daily Radar service 內部選出 multi-track universe，批次讀取 selected symbols 的 shared background context cache，補齊 selected symbols 缺少的 OHLCV，執行 Stage 1/2 scoring。
4. 後端寫入 run log 與 candidates。
5. 前端 `GET /daily-radar/latest` 顯示最新完成版本。

內部端點需使用 internal token 驗證。token 放在 GitHub Actions secrets 與 Zeabur environment variables，不寫入 repo。

Phase 2A 另有獨立 workflow `.github/workflows/daily-radar-chip-context.yml`，以排程呼叫 `POST /internal/daily-radar/chip-context/update`。這是 weekly major holders、lending 與 full margin context 的正式背景更新路徑；本機 script 只能作為除錯輔助，不是正式更新路徑。

### 12.2 運維要求

| 項目 | 要求 |
| ---- | ---- |
| Idempotency | 同一 `run_date` 重跑需覆寫或建立新版本，策略需明確 |
| Observability | 保存 universe count、prefilter count、candidate count、錯誤摘要 |
| Partial failure | 單一資料源失敗不得讓全流程無聲失敗，需標示 data gap |
| Request budget | TWSE RWD fund reports 用 report-level 查詢建立法人 tracks；yfinance 只對缺資料的 selected symbols 做一次 batch download；market index 只抓固定 benchmark；weekly major holders、lending 與 full margin context 只由背景 endpoint 更新 cache |
| Stale guard | 若核心資料日期過舊，run status 應標示 `stale_data` |
| Background context failure | 背景更新失敗會記錄在 chip-context update response/log，GitHub Actions 對非 completed 業務狀態 fail job 以利監控；existing daily run 不阻塞，daily run 對 missing/stale cache 保留 trace |
| Background labels | `background_context_labels` 只描述背景脈絡與資料完整度，不作為 score driver、交易 action 或 portfolio recommendation |

---

## 13. 測試需求

### 13.1 後端測試

| 類型 | 必測內容 |
| ---- | -------- |
| Unit tests | 前置濾網、bucket 判定、分數計算、風險扣分、冷卻邏輯 |
| Snapshot tests | 固定輸入資料產生穩定 candidate、內部 score breakdown 與 user-facing 觀察等級 |
| API tests | `/internal/daily-radar/run`、`/daily-radar/latest`、日期查詢、symbol 查詢 |
| Persistence tests | run 與 candidate 寫入、重跑行為、依日期與 symbol 查詢 |
| Data freshness tests | stale data、缺法人資料、缺融資資料時的降級行為 |
| Calibration tests | 固定 fixture 重跑產出穩定 calibration report，並覆蓋 rank cutoff、bucket threshold、risk/overheat、relative strength 與 skip reasons |
| Background context tests | migration/model、repository upsert/read、internal updater endpoint、workflow request budget、daily run 只讀 cache 且不改 ranking |
| Background label tests | API/schema 可表示 labels、freshness、missing reason；移除 background context 後 score/ranking 不變 |

### 13.2 前端測試

| 類型 | 必測內容 |
| ---- | -------- |
| Component tests | bucket tabs、candidate card、risk labels、detail drawer |
| State tests | loading、empty、error、stale data、completed |
| Copy tests | 不出現交易命令語言 |

### 13.3 手動驗收

1. 使用固定 fixture 跑出至少四個 bucket 各一筆候選。
2. 確認前端預設以觀察等級、風險標籤與命中規則呈現；分數拆解若存在，只在 advanced trace / detail 中顯示。
3. 確認重跑同一天不產生重複資料。
4. 確認缺資料時前端顯示 data gap，而不是高分推薦。
5. 確認所有文案維持觀察語言。
6. 執行 fixture calibration command，確認 report 可重跑、可 diff，且資料不足會列出 skip reason。

---

## 14. Deferred scope

MVP 不做以下事項：

1. 不做借券訊號。
2. 不做 weekly big-holder/shareholding radar。
3. 不把 `TaiwanStockHoldingSharesPer` 或 stockholder distribution 當每日訊號。
4. 不做完整產業相對強弱排名。
5. 不用 AI/LLM 做股票選擇或排名。
6. 不輸出交易命令、目標價或承諾式勝率。
7. 不改寫 `/analyze/position` 的持股診斷語意。
8. Shared evidence/context 已在 Phase 2C/2D 接入 `/analyze`、`/analyze/position`、portfolio diagnosis 與 lifecycle review；後續仍不可把它轉成交易 action、portfolio action 或 lifecycle verdict driver。

---

## 15. Open decisions

| 決策 | 預設方向 | 需確認事項 |
| ---- | -------- | ---------- |
| 掃描 universe | live default 使用 multi-track selected universe | 是否後續擴展到完整上市櫃全市場掃描 |
| Top N | Stage 2 先取 Top 100 或 Top 200 | Zeabur 執行時間與外部 API quota |
| 流動性門檻 | 先用 20 日平均成交金額 | 實際門檻需用回測或人工抽樣校準 |
| 重跑策略 | 同日重跑覆寫 latest version | 是否保留每次 run version |
| 市場指數 | 先用加權指數 | 是否納入櫃買指數作上市櫃分流 |
| 通知 | MVP 先只做頁面查詢 | 是否後續加 email、LINE 或 webhook |
| Shared evidence consumers | Phase 2D 已讓 portfolio diagnosis 與 lifecycle review 以 read/reference 方式引用 shared context；lifecycle 使用 event-date point-in-time filter | Phase 2E release gate 驗證 shared context 不覆寫 action、verdict、classification 或 ranking |

---

## 16. MVP Definition of Done

1. GitHub Actions 可呼叫 Zeabur 內部端點完成每日 run。
2. 後端可用 deterministic rule-based pipeline 產出候選清單。
3. 每筆候選都有 bucket、觀察等級或內部排序分數、風險標籤、規則解釋、market regime、relative strength trace 或缺資料原因、版本資訊與資料日期。
4. 結果可持久化並依日期、symbol 查詢。
5. React 前端可呈現最新 Daily Radar、bucket 篩選與細節抽屜。
6. 所有選股、排名與 bucket 判定不呼叫 LLM。
7. 文案符合觀察清單定位，不出現買賣命令式建議。
