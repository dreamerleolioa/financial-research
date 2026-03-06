# AI Stock Sentinel 實作任務拆解（Execution Plan）

> 版本：v1.2  
> 更新日期：2026-03-04

## 0) 範圍說明

本文件依據 [ai-stock-sentinel-architecture-spec.md](ai-stock-sentinel-architecture-spec.md) 拆解成可執行工程任務，供開發排程與追蹤。

---

## Phase 1：核心資料流（MVP Backend）

### P1-1 股票快照抓取
- **目標**：可輸入股票代碼，取得基本股價快照
- **任務**
  - `yfinance` 抓取 current/open/high/low/volume/recent closes
  - 轉成統一資料模型
- **DoD**
  - CLI 可執行，回傳 `snapshot` JSON
  - 支援 `--symbol`
- **完成記錄**：`YFinanceCrawler` + `StockSnapshot` 已實作，CLI 可執行

### P1-2 新聞清潔工（Cleaner Agent）
- **目標**：輸入新聞內容，輸出乾淨 JSON
- **任務**
  - 定義 schema：`date/title/mentioned_numbers/sentiment_label`
  - 實作 LLM 結構化輸出
  - 無 API key 時 fallback heuristic
- **DoD**
  - 支援 `--text` / `--file` / stdin
  - 任何輸入都能穩定輸出 schema
- **完成記錄**：`FinancialNewsCleaner` 已實作，LLM + heuristic fallback，支援 `--text` / `--file` / stdin

### P1-3 Crawler + Cleaner 整合
- **目標**：同一流程同時輸出股票快照與新聞清潔結果
- **任務**
  - 將 `news_cleaner` 接入 `StockCrawlerAgent`
  - `main.py` 增加 `--news-text` / `--news-file`
- **DoD**
  - 單一指令可得到 `snapshot + analysis + cleaned_news`
- **完成記錄**：`StockCrawlerAgent` 整合 `news_cleaner`，`main.py` 支援 `--news-text` / `--news-file`

---

## Phase 2：LangGraph 協作與補抓回圈

### P2-1 導入 LangGraph 狀態機
- **目標**：從線性流程升級為可回饋流程
- **任務**
  - 定義 graph state（symbol/news/fundamental/data_sufficiency）
  - 建立節點：`crawl -> clean -> analyze -> judge -> (loop)`
- **DoD**
  - 可觸發「資料不足」分支並重新抓取
- **完成記錄**：GraphState + stub 節點 + loop guard 已實作，測試覆蓋，2026-03-03

### P2-2 資料完整性判斷節點
- **目標**：分析前自動判斷資料是否足夠
- **任務**
  - 規則：必要欄位缺失、新聞過舊、數字不足
  - 產出 flags：`requires_news_refresh`, `requires_fundamental_update`
- **DoD**
  - 有明確缺失理由與重跑次數上限
- **完成記錄**：judge_node 三條規則（snapshot 缺失、新聞過舊、數字不足）已實作，reason flags 加入 GraphState，測試覆蓋，2026-03-03

### P2-4 將 Graph 接進 API
- **目標**：讓 `/analyze` 真正走 LangGraph 回圈，而非舊的線性流程
- **任務**
  - `api.py` 改用 `build_graph` 取代 `StockCrawlerAgent`
  - 對齊 `AnalyzeResponse` 回傳欄位（graph 最終 state → response）
  - 更新 API 測試
- **DoD**
  - `POST /analyze` 觸發後，judge/fetch_news/retry 回圈實際執行
  - 測試覆蓋（mock graph 或 mock 各節點依賴）
- **完成記錄**：`api.py` 改用 `get_graph` dependency（`build_graph_deps()` in `main.py`），`AnalyzeResponse` 欄位不含 `raw_news_items`，graph errors 傳遞到 response，測試 8 個（mock compiled graph），2026-03-03

### P2-3 新聞資料源擴充（RSS）
- **目標**：不只靠手動輸入新聞
- **任務**
  - Google News RSS 抓取器
  - 標準化來源 metadata
- **DoD**
  - 指定 symbol 可拉回至少 N 篇新聞
- **完成記錄**：RssNewsClient（stdlib，無外部依賴）+ fetch_news_node + GraphState.raw_news_items 已實作，測試覆蓋（6 tests in test_rss_news_client.py + 5 fetch_news_node tests in test_graph_nodes.py），2026-03-03

---

## Phase 3：分析能力強化

> 本階段以三個分析模式為核心設計依據（完整規格見 [architecture-spec §3.2](ai-stock-sentinel-architecture-spec.md)）：
> - **模式一**：交叉驗證邏輯（Cross-Verification）
> - **模式二**：技術指標定性化（Quant to Qual）
> - **模式三**：籌碼歸屬分析（Institutional Flow Profiling）

### P3-0 籌碼資料源確認（前置、Blocking P3-3）
- **目標**：確認三大法人 + 融資融券資料源可用性，解除 P3-3 的 blocking 依賴
- **任務**
  - 建立 Provider 抽象層：`InstitutionalFlowProvider`
  - 實作 `FinMindProvider`（Primary）
  - 實作 `TwseOpenApiProvider`（Fallback #1，上市優先）
  - 實作 `TpexProvider`（Fallback #2，上櫃補齊）
  - 在 Provider Router 內加入 `.TW/.TWO` 市場自動分流邏輯（`.TW`→TWSE，`.TWO`→TPEX）
  - 固定資料源優先序：`FinMind -> TWSE OpenAPI -> TPEX`
  - Provider 層強制 Schema Mapping（統一輸出欄位），並加入限流/欄位漂移防禦
  - 在 `backend/utils/` 新增驗證腳本，先驗證 `2330.TW` 能抓到三大法人欄位
  - 補一個上櫃標的 smoke test（例：`6488.TWO`）驗證分流生效
  - 記錄資料源選擇依據與限制（更新頻率、限流、欄位完整度）
- **DoD**
  - 可抓到 2330 最近 5 日：`foreign_buy`、`investment_trust_buy`、`dealer_buy`、`margin_delta`（僅 smoke test；正式分析視窗至少 `days>=20`，建議 `days=60`）
  - 上市/上櫃路徑皆可運作（至少各 1 檔驗證）
  - Provider Router 可在 Primary 失敗時依固定優先序自動切換 Fallback
  - 不同資料源皆輸出一致 schema；欄位漂移不造成流程中斷
  - 確定採用的套件/API，記錄在進度追蹤文件

### P3-1 去情緒化分析流程
- **目標**：輸出「事實」與「情緒詞標記」（對應模式一的新聞面訊號來源）
- **任務**
  - 建立情緒詞字典（中英）
  - 抽取數字 + 情緒詞標記 + 事實敘述
  - Cleaner 輸出需包含結構化 `sentiment_label`（`positive / negative / neutral`），供後續交叉驗證使用
- **DoD**
  - 回傳結構包含 `facts`, `emotional_terms`, `fact_only_summary`, `sentiment_label`
  - `sentiment_label` 為明確分類值，不是自由文字

### P3-2 Quant to Qual 預處理節點（模式二）
- **目標**：在 LangGraph 流程中新增 `preprocess_node`，於 `analyze_node` 之前執行，將技術指標數值轉換為 LLM 可直接理解的敘事描述
- **任務**
  - 實作 `generate_technical_context(df_price, inst_data)` 作為 ContextGenerator（純 rule-based）
    - 產出 `technical_context`（技術面敘事）
    - 產出 `institutional_context`（法人敘事）
  - 實作 `quantify_to_narrative(technical: dict) -> str`（純 rule-based，不呼叫 LLM）
    - 乖離率（BIAS）：定性為「短線弱勢 / 乖離過大 / 正常」
    - RSI：定性為「超買 / 超賣 / 中性」
    - 均線排列：定性為「多頭 / 空頭 / 糾結」
    - 成交量變化：定性為「量能放大 / 萎縮 / 正常」
  - 在 LangGraph builder 中插入 `preprocess_node`（位置：`calculate_indicators_node` → `preprocess_node` → `analyze_node`）
  - Prompt template 改為接受敘事字串，移除直接傳入原始數字的欄位
- **DoD**
  - `quantify_to_narrative` 有獨立單元測試（覆蓋邊界值：BIAS <-5、>8；RSI <30、>70）
  - LangGraph 流程跑完後，`analyze_node` 的 prompt context 中不含裸數值，只含定性描述

### P3-2b Tool Use 計算工具（原 P3-2）
- **目標**：Agent 可呼叫計算工具驗證指標，確保數值來源可追溯
- **任務**
  - `calculate_technical_indicators(symbol, period)`：MA5/20/60、BIAS、RSI14、Volume Change、20 日高低點
  - `calculate_price_levels(symbol, window=20)`：`high_20d`、`low_20d`、`support_20d`、`resistance_20d`
  - `calculate_bias(close, ma)`：乖離率公式
  - `estimate_pe_percentile(symbol, pe)`：本益比歷史百分位
  - `calculate_growth_rate(current, previous)`：YoY / MoM
  - 接入 Analysis Agent 的 tool call
- **DoD**
  - 至少 4 個工具有對應單元測試（含 `calculate_price_levels`）
  - `entry_zone` 與 `stop_loss` 來源可追溯至工具輸出（不可由 LLM 自行生成數值）

### P3-3 籌碼歸屬分析 + 信心分數（模式一 + 模式三）
- **前置依賴**：P3-0 完成（資料源確認）
- **目標**：實作三維訊號交叉驗證，輸出有依據的信心分數
- **任務**
  - 實作 `fetch_institutional_flow(symbol, days)` 工具
    - 輸出：`foreign_net_cumulative`, `trust_net_cumulative`, `dealer_net_cumulative`, `three_party_net`, `consecutive_buy_days`, `margin_balance_delta_pct`, `flow_label`
    - `flow_label` 由 rule-based Python 決定，不由 LLM 判斷
  - 實作 `adjust_confidence_by_divergence(base_score, news_sentiment, inst_flow, technical_signal)`
    - `base_score` 從 **50**（中性基準）開始，clamp 至 [0, 100]
    - 三維共振 → +15；利多出貨背離 → -20；散戶追高 → -15；利空不跌 → +10
    - 回傳 `(adjusted_score, cross_validation_note)`
    - **純 Python，不呼叫 LLM**；`cross_validation_note` 為 rule-based 固定字串
  - Graph flow：`preprocess → score → analyze → strategy → END`
    - `score_node` 在 `analyze_node` **之前**執行，讓 LLM prompt 可讀取 `confidence_score` 與 `cross_validation_note`
    - LLM 在 `analyze_node` 中讀取這兩個欄位，用於生成 `risks` / `summary`，**不得修改分數**
  - 更新 system prompt 為 Skeptic Mode：強制「提取 → 對照 → 衝突檢查 → 僅輸出事實與邏輯推論」
  - `GraphState` 新增欄位：`confidence_score`, `cross_validation_note`
- **DoD**
  - `adjust_confidence_by_divergence` 對四種情境均有測試（共振 / 利多出貨 / 散戶追高 / 利空不跌）
  - `fetch_institutional_flow` 可抓到真實資料並輸出 `flow_label`
  - 最終輸出包含 `confidence_score`（0~100）與 `cross_validation_note`（非空字串）
  - 最終輸出包含 `strategy_type`、`entry_zone`、`stop_loss`、`holding_period`（Task C3 已完成）

---

## Phase 4：前端儀表板（React + Tailwind）

### P4-1 前端基礎專案
- **目標**：建立可執行前端骨架
- **任務**
  - 建立 React + TypeScript + Tailwind
  - 股票輸入框 + 查詢按鈕
- **DoD**
  - 可輸入 symbol 並呼叫後端 API
- **完成記錄**：React + TypeScript + Tailwind 骨架已建立，API 串接完成（2026-03-04 Session 7）

### P4-2 核心展示元件
- **目標**：呈現 AI 分析價值
- **任務**
  - 信心指數圓形元件
  - 雜訊過濾左右對照
  - 分析路徑（step timeline）
- **DoD**
  - 三個元件可顯示真實後端資料
- **完成記錄**：信心指數圓弧改用 `confidence_score`（真實資料，動畫過渡）；快照資訊、cleaned_news 摘要改為真實資料驅動（2026-03-04 Session 7）

### P4-3 體驗優化
- **目標**：提升可讀性與可追溯性
- **任務**
  - loading 狀態（按鈕 disabled + 文字）
  - error banner（errors[0] 顯示於頁面頂部）
- **DoD**
  - loading / error 狀態正確顯示，不阻斷主頁
- **完成記錄**：loading 狀態、紅色 error banner 均已實作（2026-03-04 Session 7）

### P4-4 操作建議卡片（Action Plan）
- **目標**：讓使用者輸入代碼後可直接看到可執行策略
- **任務**
  - 在頁面底部新增全寬「戰術行動（Action Plan）」卡片
  - 展示欄位：`strategy_type`、`entry_zone`、`stop_loss`、`holding_period`
  - `cross_validation_note` 顯示於信心指數卡片下方
- **DoD**
  - 卡片可正確顯示「策略方向 / 建議入場區間 / 防守底線 / 預期持股期間」
  - null 值顯示 —，不崩潰
- **完成記錄**：Action Plan 全寬 2×2 卡片、`cross_validation_note` 灰色小字均已實作（2026-03-04 Session 7）

### P4-6 Action Plan 紅綠燈標籤（待排程）
- **目標**：在 Action Plan 卡片加入燈號標籤，一眼判讀訊號強弱
- **架構原則**：燈號由後端 rule-based Python 計算並回傳 `action_plan_tag`，前端**不含**任何條件判斷，僅做 enum → emoji/文字的純顯示
- **後端任務**
  - 實作 `calculate_action_plan_tag(rsi14, flow_label, confidence_score) -> str`（純 Python rule-based）
  - 判斷規則（固定優先序）：`opportunity`（rsi14 < 30 + institutional_accumulation + confidence > 70）/ `overheated`（rsi14 > 70 + distribution）/ `neutral`（其餘）
  - 任一輸入為 None 時安全降級回 `neutral`
  - `GraphState` 新增 `action_plan_tag` 欄位；`AnalyzeResponse` 新增 `action_plan_tag: str | null`
  - 將 `flow_label` 作為頂層欄位 `institutional_flow` 提升至 API Response
- **前端任務**
  - Action Plan 卡片標題旁顯示燈號標籤（`opportunity` → 🟢 機會 / `overheated` → 🔴 過熱 / `neutral` → 🔵 中性）
  - `action_plan_tag` 為 null 時不顯示標籤
- **DoD**
  - `calculate_action_plan_tag` 有獨立單元測試（三情境 + None 安全）
  - API 穩定回傳 `action_plan_tag` 與 `institutional_flow`
  - 前端可正確顯示三種燈號（含 null fallback 不崩潰）

### P4-5 新聞摘要品質優化（規格補強，待排程）
- **目標**：提升 `cleaned_news` 可用性與可信度，避免時間戳/日期碎片被誤當摘要重點
- **任務**
  - 在 `clean_node` 增加品質檢查規則：辨識並排除「標題為時間戳／URL／來源代碼」
  - 日期標準化：優先輸出來源日期（ISO 8601 / RFC 2822），無法解析時標記 `DATE_UNKNOWN`
  - 關鍵數字過濾：排除純日期碎片，保留財經語意數值（漲跌幅、金額、EPS、目標價、量價）
  - 新增 `cleaned_news_quality` 欄位（`quality_score` + `quality_flags`）供 API 與前端使用
  - 前端在低品質摘要顯示「摘要品質受限」提示，避免誤導
- **DoD**
  - 不再出現以純時間戳作為摘要標題
  - `date=unknown` 時必有品質旗標，且 UI 顯示品質提示
  - `mentioned_numbers` 中日期碎片比例明顯下降（由測試案例驗證）
  - 新增單元測試（cleaner quality rules）與整合測試（API response quality flags）
- **完成記錄**：NQ-1~NQ-6 全數完成（2026-03-05）；`news_display` 拆分 ND-1~ND-5 同步完成（`GraphState` 新增 `news_display` 欄位、`quality_gate_node` RFC 2822→ISO 日期正規化、`api.py` 新增 `news_display` 欄位、前端改讀 `news_display` 渲染新聞卡片並移除 `mentioned_numbers` chips）；205 tests passed

### P5-1 信心分數可靠性優化（Confidence Score Reliability）

- **問題背景**：目前多數查詢固定輸出 50（中性基準），核心原因為：
  1. `_derive_technical_signal` 判斷 `bullish` 過嚴（需三條件同時成立），多數退化 `sideways`
  2. 籌碼 Provider 在無 API key / 限流時 `flow_label` 固定 `neutral`
  3. 四條規則為精確命中才調分，三訊號均 `neutral/sideways` 時 adjustment = 0
- **目標**：信心分數能反映實際技術面強弱，不再固定輸出 50
- **任務**
  - CS-1：`_derive_technical_signal` 改為多因子加權（RSI 位置 / BIAS / 均線排列各自獨立貢獻分量）
  - CS-2：`adjust_confidence_by_divergence` 改為多維加權模型（partial match 可得部分調分；引入 `rsi14`、`bias_ma20` 數值直接計算貢獻）
  - CS-3：機構資料缺失時以 `unknown` 旗標排除該維度，由剩餘維度計算（調整置信幅度，避免拉低分數）
  - CS-4：回傳結構拆分 `data_confidence`（資料完整度）與 `signal_confidence`（訊號強度），前端可顯示資料不足提示
  - CS-5：補齊單元測試（新規則覆蓋 + 回歸四原始情境）
- **DoD**
  - 對相同股票查詢，技術指標方向明確時分數應偏離 50（至少 ±10）
  - 機構資料缺失時分數不因此固定停在 50
  - `data_confidence` 與 `signal_confidence` 作為 API 新欄位回傳
  - 既有信心分數四情境測試全數通過（回歸保護）
- **完成記錄**：CS-1~CS-5 全數完成（2026-03-05）；`derive_technical_score()` 多因子加權取代 AND 條件；`compute_confidence()` 整合回傳 `data_confidence`/`signal_confidence`；`score_node` 全面整合；229 tests passed

### P4-7 信心指數資料不足提示（前端）

- **目標**：`data_confidence < 60` 時在信心指數卡片下方顯示「資料不足，分數僅供參考」灰色提示
- **任務**
  - 前端讀取 API 回傳的 `data_confidence` 欄位（後端已實作，CS-4）
  - `data_confidence < 60` 時卡片下方顯示灰色提示文字
  - `data_confidence` 為 null 時不顯示，不崩潰
- **DoD**
  - 提示文字正確出現於信心指數卡片下方
  - null 安全，不崩潰
- 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md` Session 4

### P4-8 消息面職責邊界 + 多筆新聞列表

- **目標**：修正消息面職責定義（新聞僅負責情緒訊號，不負責財務數字）；`news_display` 升級為最多 5 筆陣列
- **架構原則**：新聞（消息面）負責市場情緒訊號（法說會、政策、法人評等），財務數字（EPS/營收/毛利率）屬基本面，不應由 Quality Gate 扣分
- **後端任務**
  - NM-1：`NO_FINANCIAL_NUMBERS` flag 計分貢獻改為 0（旗標保留但不扣 `quality_score`）
  - NM-2：LLM System Prompt 移除「從新聞提取財務數字」要求，聚焦事件情緒語義
  - NM-3：`GraphState` `news_display` → `news_display_items: list[dict]`
  - NM-4：`quality_gate_node` 迭代 `raw_news_items[:5]`，產出 `news_display_items` 陣列
  - NM-5：`api.py` `AnalyzeResponse` 欄位更新（`news_display` → `news_display_items`）
  - NM-7：補齊測試（state 欄位、node 輸出、API 欄位、quality_score 計分）
- **前端任務**
  - NM-6：新聞卡片改為多筆列表，每筆可點擊連結 + 公開資訊觀測站提示
- **DoD**
  - `news_display_items` 為陣列，最多 5 筆，每筆含 `title`/`date`/`source_url`
  - `NO_FINANCIAL_NUMBERS` 旗標保留但不扣 `quality_score`
  - 前端正確顯示多筆新聞連結（含 null 安全）
  - 補齊測試（7 個子任務均有對應測試案例）
- 計劃文件：`docs/plans/2026-03-06-news-scope-and-display-items.md`

### P5-2 分析敘事結構化（Session 8）

> 計劃文件：`docs/plans/2026-03-07-dimensional-analysis.md`

- **目標**：將 LLM 分析輸出從單一 `summary` 升級為三維獨立段落 + 綜合仲裁，提升可解釋性
- **任務 A（後端 Schema）**：`AnalysisDetail` dataclass 新增 `tech_insight`、`inst_insight`、`news_insight`、`final_verdict` 欄位（均為 `str | None`，向後相容）
- **任務 B（後端 Prompt）**：更新 `langchain_analyzer.py` JSON 輸出要求，強制 LLM 分段輸出四欄位，禁止跨維度混寫（參考架構規格 §3.2 分維度 System Prompt 規範）
- **任務 C（前端 UI）**：「LLM 分析報告」卡片改為分欄式配置——技術面/籌碼面/消息面三張小卡 + 綜合仲裁全寬卡；各卡標題旁附維度燈號
- **DoD**
  - `AnalysisDetail` 含四個新欄位，舊欄位不破壞
  - LLM 輸出的 prompt 包含分維度限制指令；`_parse_analysis()` None-safe
  - 前端可正確渲染三維小卡（含 null 降級不崩潰）
  - `make test` 全數通過
- **架構原則**：`tech_insight` / `inst_insight` / `news_insight` 均由 LLM 生成，但 `confidence_score` 仍由前置 `score_node` rule-based 計算，LLM 不得修改

---

## 補強任務（Spec Gap Fix + 邏輯缺口）

> 來源：2026-03-05 規格對比發現 + 邏輯缺口追蹤  
> 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md`

### SG-1 技術位階指標（Support / Resistance）

- **目標**：補齊 `high_20d` / `low_20d` / `support_20d` / `resistance_20d`，讓 `entry_zone` / `stop_loss` 輸出實際價格而非描述性文字
- **任務**
  - `yfinance_client.py` `StockSnapshot` 新增四個計算欄位（近 20 日最高/低點、均量加權支撐/壓力位）
  - `context_generator.py` `generate_technical_context()` 新增支撐/壓力位敘事段落
  - `strategy_generator.py` `entry_zone` / `stop_loss` 改為以實際價格計算
  - `GraphState` 新增四個選填欄位
  - **Fallback**：`low_20d` / `ma60` 不可用時，`entry_zone` 回傳 `"資料不足，建議參考現價 +/- 5%"`；禁止虛構數值
- **DoD**
  - `entry_zone` 輸出含實際價格數值或說明 fallback 原因，不再輸出純描述性文字
  - 補齊測試（含 fallback 安全測試）
- 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md` Session 1

### SG-3 AnalyzeResponse 欄位完整性

- **目標**：補齊多個規格要求的頂層欄位
- **任務**
  - `AnalyzeResponse` 新增 `sentiment_label: str | null`（從 `cleaned_news.sentiment_label` 浮出）
  - `AnalyzeResponse` 新增 `data_sources: list[str]`（依實際成功抓取的來源動態填入）
  - `AnalyzeResponse` 新增 `action_plan: dict | null`（rule-based 計算，含 `action`/`target_zone`/`defense_line`/`momentum_expectation`）
- **DoD**
  - API 穩定回傳上述三個欄位
  - 補齊測試
- 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md` Session 3

### SG-4 AnalysisDetail 結構強化

- **目標**：`AnalysisDetail` 新增 `institutional_flow` 與 `sentiment_label` 欄位，與規格保持一致
- **任務**
  - `AnalysisDetail` dataclass 新增 `institutional_flow: str | None` 與 `sentiment_label: str | None`
  - LLM System Prompt 同步更新，要求輸出上述欄位（展示用，LLM 不得修改 `confidence_score`）
- **DoD**
  - `analysis_detail` JSON 含 `institutional_flow` 與 `sentiment_label` 欄位
  - 補齊測試
- 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md` Session 3

### SG-5 LLM Prompt 三維消息面輸入

- **目標**：補齊 `langchain_analyzer.py` `_HUMAN_PROMPT` 缺少消息面資料的缺口，讓 Skeptic Mode 四步驟處理真正三維輸入
- **任務**
  - `_HUMAN_PROMPT` 加入 `{news_summary}` 欄位（取 `cleaned_news.title` + `mentioned_numbers` 或 `news_content` 原文）
  - `analyze()` 簽名新增 `news_summary: str | None = None` 參數
  - `analyze_node` 從 `state["cleaned_news"]` 組合後傳入
  - 補齊測試（含有/無 `cleaned_news` 兩情境）
- **DoD**
  - LLM prompt 包含三維輸入（技術面敘述 + 籌碼 JSON + 新聞摘要）
  - 補齊測試
- 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md` Session 5

### SG-6 `data_confidence` 語義修正

- **問題**：`compute_confidence()` 把 `news_sentiment="neutral"` 與 `technical_signal="sideways"` 計為資料缺失，但這兩者是成功計算後的合法輸出值，不代表資料不足
- **目標**：修正 `data_confidence` 語義，改為「是否成功取得資料」而非「訊號是否有方向」
- **任務**
  - 修正判斷邏輯：`inst_flow != "unknown"` → 有籌碼資料；`news_sentiment` 有值（含 `neutral`）→ 有新聞資料；`closes` 足夠筆數 → 有技術資料
  - 或考慮現有邏輯改名 `signal_breadth`，新增真正的 `data_confidence`
  - 補齊測試（`neutral` 情緒 → `data_confidence` 不應為 0 或 33）
- **DoD**
  - `data_confidence` 語義與規格一致：反映資料完整度而非訊號方向
  - 補齊測試（回歸保護）
- 計劃文件：`docs/plans/2026-03-06-spec-gap-fix.md` Session 6

---

## Cross-cutting（跨階段）

### C-1 API 服務層（FastAPI）
- 建立 `/analyze`、`/health`
- 後續可加 `/events`（SSE）

### C-2 可觀測性與日誌
- 統一 request_id
- 節點耗時、失敗原因

### C-3 測試策略
- 單元測試：cleaner、計算工具
- 整合測試：crawler -> cleaner -> analyzer

### C-4 完成即補測試（新增）
- **目標**：每一項功能完成後，立即補上對應測試，避免功能與測試脫鉤
- **任務**
  - 功能 PR 必須包含至少一個對應測試（單元或整合）
  - 若暫時無法補測試，需在 PR 註明原因與補測試 ETA
  - Checkpoint 需檢查「本週新增功能是否都有測試」
- **DoD**
  - 每個已完成任務都能對應到至少一個測試案例
  - CI/本地測試可驗證新增功能行為

### C-5 策略模板（Task C3，新增）
- **目標**：強制 AI 輸出具備防守思維的策略欄位，而非空泛建議
- **任務**
  - 建立策略模板對應表（`short_term` / `mid_term` / `defensive_wait`）
  - 以 Python 規則綁定 `entry_zone`、`stop_loss`、`holding_period`
  - Prompt 明確要求輸出「防守底線與觸發條件」（例如破 MA60）
- **DoD**
  - `analysis_detail` 固定帶出策略欄位且不為空
  - 單元測試覆蓋至少 2 種訊號組合對應的策略模板

---

## 建議執行順序（短版）

1. 完成 P2-1 + P2-2（先有 LangGraph 回圈）
2. 完成 P2-3（新聞來源自動化）
3. **[High] 先做 P3-0**（Provider 抽象 + 2330.TW 實測，解除 P3-3 blocking）
4. **[Medium] 完成 P3-2**（`preprocess_node` + `generate_technical_context` + `quantify_to_narrative`）
5. **[Medium] 完成 P3-3**（Skeptic Prompt + 衝突規則算分 + `fetch_institutional_flow`）
6. 完成 P3-1（Cleaner 輸出 `sentiment_label`）
7. 完成 P3-2b（Tool Use 計算工具）
8. **[Low] 完成 P4-1~P4-3**（前端展示）

> 註：以上為歷史開發順序。依 2026-03-05 目前狀態（P1~P5-1 + P4-5 NQ/ND 全數完成，229 tests passed），**下一步建議執行順序為：P4-8（消息面職責界定 + 多筆新聞）→ P4-7（data_confidence 前端提示）→ SG-1（技術位階指標）→ P4-6（Action Plan 燈號）→ SG-3（AnalyzeResponse 欄位）→ SG-5（LLM Prompt 三維輸入）→ SG-6（data_confidence 語義修正）**。
