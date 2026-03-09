# AI Stock Sentinel 技術架構需求文件

> 日期：2026-03-07
> 狀態：Draft v2.5
> 目的：將產品需求大綱轉為可落地的工程實作藍圖
> 更新摘要：修補邏輯衝突與規格缺口——統一籌碼分數定義（移除舊表分數欄）、修正「利空不跌」調整量為 0、補 derive_technical_score 邊界說明、消除 technical_signal/institutional_flow unknown 定義歧義、修正 Prompt 步驟一角色描述、補 action_plan/holding_period/news_display_items Schema、統一消息面職責說明引用、移除 twstock 遺留項、修正 4.1 節編號錯誤；補 _price_level_narrative 邊界規範（>= / <= + 2% 緩衝）；新增 DATE_UNKNOWN rule-based -3 懲罰規則；新增 Session 8 分維度拆解分析——`AnalysisDetail` 三維獨立欄位、LLM 分段 Prompt、前端分欄卡片 UI

## 1. 目標與方向

AI Stock Sentinel 採用 TypeScript + Python 混合架構，核心目標為：

- 建立可循環補資料的多 Agent 流程
- **全方位數據偵察**：整合新聞、技術指標、籌碼三大維度，進行多維交叉驗證，降低單一訊號誤判風險
- 嚴格落實 **Tool Use（工具化計算）**：嚴禁 LLM 盲猜數值，所有技術指標與籌碼數據必須透過 Python 函式計算後，再交由 LLM 進行定性分析
- 將分析過程透明化，前端可視化呈現 AI 決策路徑

### 1.1 三大分析維度

| 維度 | 說明 | 資料來源 |
|------|------|----------|
| **消息面 (News)** | 影響市場情緒的事件訊號（法說會、政策、產業動態、法人評等調整等），**不涵蓋公司財務數字**（財報數字屬於基本面，需另從財報資料源取得） | Google News RSS、財經媒體 RSS |
| **技術面 (Technical)** | MA5/20/60 均線、乖離率 (BIAS)、RSI、成交量變化 | yfinance + Pandas 計算 |
| **籌碼面 (Institutional)** | 三大法人（外資、投信、自營商）買賣超、融資融券消長 | FinMind（Primary）+ TWSE OpenAPI / TPEX（Fallback） |

建議以 **LangGraph（LangChain 延伸）** 作為 Agent 協作框架，以支援非線性流程與反饋迴圈。

---

## 2. 系統架構（建議）

### 2.1 後端分層

- **Orchestration Layer**
  - 技術：LangGraph
  - 責任：控制 Agent 之間的流程、條件分支、重試與補抓

- **Data Acquisition Layer**
  - 責任：抓取股票基本面、新聞、法人/行情補充資料
  - 產出：標準化原始資料（Raw + Metadata）

- **Data Cleaning & Structuring Layer**
  - 責任：去除 HTML 雜訊、抽取結構化欄位
  - 產出：可供分析的乾淨 JSON

- **Analysis Layer**
  - 責任：事實萃取、情緒字眼標記、指標計算、結論生成、語義化翻譯（術語→直白中文）
  - 產出：可解釋分析結果（含信心分數與依據）

- **API Layer（Python FastAPI / Node BFF）**
  - 責任：提供前端查詢、回傳分析過程事件流

### 2.2 反饋迴圈（LangGraph）

核心邏輯：

1. 先抓資料 → 2) 分析完整性 → 3) 缺資料則回到爬蟲補抓 → 4) 再分析 → 5) 輸出

建議在 Graph 中加入條件節點：

- `is_data_sufficient`
- `requires_news_refresh`
- `requires_fundamental_update`

---

## 3. Agent 設計

## 3.1 Crawler Agent（偵察兵）

### 角色

負責蒐集分析所需的**消息面、技術面、籌碼面**三維資料，並保留來源與時間戳，供後續可追溯。

### 資料源建議

- **基本面**
  - 財報狗 / 公開資訊觀測站（優先採 API 或穩定封裝庫）
  - 目標：避免高頻爬 HTML 被封鎖

- **即時新聞（消息面）**
  - Google News RSS
  - 指定財經媒體 RSS / API
  - **範疇限定**：詳見第 1.1 節消息面職責定義——聚焦市場情緒事件訊號，不負責財務數字

- **技術面指標**（由 `yfinance` 拉取 OHLCV 後，以 **Pandas** 計算）
  - `MA5`、`MA20`、`MA60`：收盤價簡單移動平均
  - `BIAS`：乖離率，公式：`(Close - MAn) / MAn × 100`
  - `RSI14`：14 日相對強弱指標，使用 Wilder 平滑法
  - `Volume_Change`：今日成交量相較 MA5_Volume 的百分比變化
  - `High20`、`Low20`：近 20 日高低點（支撐壓力位基礎）
  - `Support_20d`、`Resistance_20d`：由近 20 日價量資料推導之支撐／壓力位

  > ⚠️ **嚴格規範**：所有數值必須由 Python 函式（pandas / ta-lib）實際計算，**禁止** LLM 自行推算或估計。

- **籌碼面（法人動向）**
  - 三大法人買賣超：外資（FINI）、投信（SITC）、自營商（Dealer）
  - 融資餘額變化（Margin Balance Delta）
  - 融券餘額變化（Short Balance Delta）
  - 資料源優先序：`FinMindProvider`（Primary）→ `TwseOpenApiProvider`（Fallback #1）→ `TpexProvider`（Fallback #2）
  - 市場分流：`.TW`（上市）優先走 TWSE 路徑，`.TWO`（上櫃）優先走 TPEX 路徑

### 抓取工具建議

- 優先：**Firecrawl**、**Browserbase**
- 原因：可直接輸出乾淨 Markdown，降低自行清理 HTML 成本

### 輸出（範例）

```json
{
  "symbol": "2330.TW",
  "fetched_at": "2026-03-03T08:00:00Z",
  "fundamentals": {},
  "news": [
    {
      "source": "google-news-rss",
      "url": "...",
      "title": "...",
      "content_markdown": "..."
    }
  ],
  "technical": {
    "ma5": 925.4,
    "ma20": 910.2,
    "ma60": 880.5,
    "bias_ma20": 1.68,
    "rsi14": 62.3,
    "volume_change_pct": 23.5
  },
  "institutional": {
    "foreign_net": 12500,
    "trust_net": -3200,
    "dealer_net": 800,
    "margin_balance_delta": 4500,
    "short_balance_delta": -1200
  }
}
```

## 3.2 Analysis Agent（參謀官）

### 角色

核心分析引擎，主責「去情緒化 + 多維交叉驗證 + 量化工具計算 + 結論生成」。  
升級後須整合消息面、技術面、籌碼面三個維度，並透過交叉驗證提升信號可信度。

### 核心分析邏輯：多維交叉驗證

**原則**：各維度訊號必須相互印證，訊號衝突時信心分數應主動調降。

| 情境 | 處理邏輯 |
|------|----------|
| 新聞利多 ＋ 外資連買 ＋ RSI 未超買 | 信心分數 ↑，標記「多方共振」 |
| 新聞利多 ＋ 外資大賣 ＋ 融資增加 | 信心分數 ↓，標記「消息與法人背離，散戶追高風險」 |
| 新聞中性 ＋ 技術均線多頭排列 ＋ 籌碼沉澱 | 信心分數維持，標記「靜默吸籌」 |
| 新聞利空 ＋ 投信連買 ＋ RSI 超賣 | 信心分數微升，標記「逆勢佈局觀察」 |

---

### 分析模式一：交叉驗證邏輯（Cross-Verification）

> AI Stock Sentinel 的核心靈魂：當各維度資料互相矛盾時，AI 的判斷邏輯。

**實作規範**：
- `news_sentiment` 由 Cleaner 結構化輸出（`sentiment_label: positive / negative / neutral`），**不由 LLM 在分析時自行判斷**
- `institutional_flow` 由籌碼工具計算（`foreign_net_cumulative` 的正負與連續天數）
- Prompt 接收的是已結構化的結論欄位，LLM 只負責邏輯推理，不負責數值判斷
- Action Plan 欄位（`entry_zone` / `stop_loss` / `holding_period`）必須由 Python 先計算出具體價位或價位區間，LLM 僅做語句整理

**典型衝突情境（訊號背離）**：

| 情境名稱 | 描述 | 處理邏輯 |
|----------|------|----------|
| 利多不漲（Bullish Divergence） | 新聞極度樂觀（如營收創高）＋ 外資/投信連續大賣 ＋ 技術面高檔長上影線 | 信心分數大幅調降；`cross_validation_note` 標記「基本面利多疑已反映，法人趁利多出貨，建議轉保守觀察」 |
| 利空不跌（Bearish Divergence） | 新聞負面 ＋ 股價守穩關鍵支撐 ＋ 大法人低調承接 | 分數維持中性（0 調整量）；觸發警示 note「逆勢佈局訊號，需觀察持續性」，不上調分數（守穩支撐為觀察訊號而非確認訊號） |
| 訊號共振（Alignment） | 三維方向一致（例：利多 ＋ 外資連買 ＋ RSI 健康） | 信心分數正常或上調；標記「多方共振，訊號可信度高」 |

**信心分數調整規則（rule-based，非 LLM 計算）**：

各維度獨立評分後加總，再套用特殊情境加成（實作見 `analysis/confidence_scorer.py`）：

| 維度 | 值 | 分數 |
|------|----|------|
| `news_sentiment` | positive / negative / neutral | +5 / -5 / 0 |
| `inst_flow` | institutional_accumulation / distribution / retail_chasing / neutral / unknown | +7 / -10 / -8 / 0 / 0 |
| `technical_signal` | bullish / bearish / sideways | +5 / -5 / 0 |
| 特殊：三維共振 | positive + accumulation + bullish | 額外 +3 |
| 特殊：利多出貨 | positive + distribution | 額外 -7 |

`cross_validation_note` 仍由固定字串對應情境產生；三維共振、利多出貨、散戶追高、利空不跌四情境的文案不變。`inst_flow = "unknown"`（Provider 全部失敗）時分數不貢獻，亦不觸發特殊情境。

**信心分數拆分（CS-4）**：
- `data_confidence`：資料完整度（0 / 33 / 67 / 100），依三個維度有無有效值計算
  > **有效值定義**（Session 6 定案）：
  > - 新聞維度：`news_sentiment` 有任何值（含 `neutral`）→ 視為有新聞資料；`neutral` 表示情緒中性，不代表資料未取得
  > - 技術維度：`technical_signal` 有任何值（含 `sideways`）→ 視為有技術資料；`sideways` 是合法計算結果
  > - 籌碼維度：`inst_flow != "unknown"` → 視為有籌碼資料（`unknown` 代表所有 Provider 失敗）
  > 簡言之：`data_confidence` 量的是「資料取得完整度」，不是「訊號偏向廣度」。
- `signal_confidence`：訊號強度（即舊 `confidence_score`），由 `adjust_confidence_by_divergence()` 計算
- `confidence_score`：保留為 `signal_confidence` 的別名，向後相容
- 整合入口：`compute_confidence(base_score, news_sentiment, inst_flow, technical_signal) -> dict`

**技術面加權分數（CS-1）**：
- `derive_technical_score(closes, rsi, bias) -> int`：RSI / BIAS / MA 排列各自獨立加權，總分映射至 [30, 70]，資料不足回傳 50

---

### 分析模式二：技術指標定性化與語義化翻譯層（Quant to Qual + Semantic Translation）

> 在呼叫 LLM 分析前，必須先透過 `preprocess_node` 將數值轉換為「敘事背景」。

**設計動機**：LLM 看到 `ma20: 850, close: 800` 無法可靠判斷意涵；但看到「股價低於月線 6%，處短線弱勢，存在超賣反彈需求」則能立即理解語義進行推理。

**架構位置**：在 LangGraph 流程中，`calculate_indicators_node` → **`preprocess_node`（新增）** → `analyze_node`

**ContextGenerator（建議命名）**：
- `generate_technical_context(df_price, inst_data)` 作為 `preprocess_node` 的核心函式
- 同時產生 `technical_context` 與 `institutional_context`
- 由 Python rule-based 生成「敘事背景」，禁止呼叫 LLM

**語義化翻譯層（已實作）**：
- 目的：將 RSI、BIAS、MA、Institutional Flow 等術語映射為投資者可讀的直白中文
- 實作位置：`analysis/context_generator.py`，`generate_technical_context(df_price, inst_data)` 為主入口
- 規範：語義判斷閾值由 Python rule-based 固定，禁止 LLM 動態改寫

> ⚠️ **實作說明（2026-03-05）**：規格原本規劃 `quantify_to_narrative()` 單一函式與 `TERM_ALIAS` 常數，實際落地改為多個獨立 helper：`_bias_narrative()`、`_rsi_narrative()`、`_ma_narrative()`、`_volume_narrative()`、`_inst_narrative()`，統一由 `generate_technical_context()` 協調呼叫。`TERM_ALIAS` 常數未採用，閾值判斷與敘事字串直接內嵌於各 helper。功能等價，可測試性相同。

| 技術術語 | 中文語義標籤（對外） |
|----------|----------------------|
| RSI | 買賣氣場（動能強弱） |
| BIAS | 股價位階（月線距離） |
| MA5/20/60 | 平均成本帶（短中長均線） |
| Institutional Flow | 法人資金流向 |

---

### 分析模式三：籌碼歸屬分析（Institutional Flow Profiling）

> 教 AI 識別「誰」在影響股價，並判斷籌碼結構健康度。

**⚠️ 前置條件**：此模式依賴三大法人每日買賣超資料（`foreign_net`、`trust_net`、`dealer_net`）與融資融券餘額，正式採用雙軌策略：`FinMindProvider` 為主來源，`TwseOpenApiProvider` 為官方備援（上櫃由 `TpexProvider` 補齊）。

### 籌碼資料源策略（Provider Abstraction）

> 原則：不得依賴單一資料源；主來源不可用時必須自動降級，避免分析流程中斷。

**Provider 介面（建議）**：

```python
from typing import Protocol

class InstitutionalFlowProvider(Protocol):
  def fetch_daily_flow(self, symbol: str, days: int) -> dict:
    ...
```

**Provider 優先序（v2）**：
1. `FinMindProvider`（Primary）：欄位完整（含三大法人 + 融資融券）
2. `TwseOpenApiProvider`（Fallback #1）：官方權威來源，優先覆蓋上市標的
3. `TpexProvider`（Fallback #2）：補齊上櫃標的

**上市 / 上櫃分流規範**：
- `symbol` 為 `.TW`：走上市路徑（TWSE provider chain）
- `symbol` 為 `.TWO`：走上櫃路徑（TPEX provider chain）
- 無後綴或未知後綴：先嘗試 FinMind，再依 mapping/metadata 推斷市場並套用對應 fallback

**Defensive Programming（Provider 層強制）**：
- 強制實作 Schema Mapping：不論來源欄位命名差異，輸出 JSON 結構必須一致
- 限流（rate limit）需有可追蹤重試與降級策略，不得直接中斷分析主流程
- 欄位漂移（field drift）需告警並保留核心欄位穩定輸出（缺漏欄位以預設值/nullable 表示）

**最小可用驗收（MVP）**：
- 可對 `2330.TW`（映射 `2330`）抓到近 5 日：`foreign_buy`、`investment_trust_buy`、`dealer_buy`、`margin_delta`（僅連通性驗收）
- 可對至少一檔上櫃標的（例：`6488.TWO`）完成路徑驗證
- 失敗時回傳可追蹤錯誤碼（`INSTITUTIONAL_FETCH_ERROR`）且不中斷主流程
- 正式分析（flow_label / confidence / strategy）需使用至少 20 日資料視窗，建議 60 日

**籌碼集中度判斷模型**：

> 信心分數調整值以下方「信心分數調整規則（v2 定案）」表格為準（+7 / -10 / -8）。本表僅描述狀態判斷觸發條件與 AI 解讀方向。

| 指標 | 資料來源 | 狀態判斷 | AI 解讀方向 |
|------|----------|----------|-------------|
| 三大法人合計連續買超（≥3 日） | TWSE OpenAPI | `institutional_accumulation` | 強力多頭支撐 |
| 外資單日大賣超（超過近 5 日均值 2 倍） | TWSE OpenAPI | `distribution` risk | 主力出貨警示 |
| 融資餘額單日增幅 >5%（相對近 10 日均值） | TWSE OpenAPI | `retail_chasing` risk | 散戶追高，籌碼混亂 |
| 融資餘額持續下降 ＋ 股價上漲 | TWSE OpenAPI | 籌碼沉澱 | 健康換手，`institutional_accumulation` 加成 |
| 成交量 > 月均量 50% ＋ 股價平漲 | yfinance | 量價平量增 | 底部換手或有人墊高成本，需搭配法人方向判斷 |

**`fetch_institutional_flow` 工具輸出規格**：

```json
{
  "symbol": "2330.TW",
  "period_days": 60,
  "foreign_net_cumulative": 15200,
  "trust_net_cumulative": -3100,
  "dealer_net_cumulative": 800,
  "three_party_net": 12900,
  "consecutive_buy_days": 3,
  "margin_balance_delta_pct": 2.3,
  "short_balance_delta_pct": -1.1,
  "flow_label": "institutional_accumulation"
}
```

> `flow_label` 由 rule-based Python 函式決定，不由 LLM 判斷。

### Prompt / CoT 設計目標

輸入：清潔後新聞 ＋ **定性化技術面敘述**（由 `quantify_to_narrative` 轉換後）＋ 籌碼 JSON（全由 Python 函式計算後傳入）

> **消息面職責**：詳見第 1.1 節。新聞維度聚焦市場情緒事件訊號，不負責財務數字（財報數字屬基本面，目前尚未實作）。若 RSS 新聞碰巧含有財務數字，可作附帶參考，但不得以「新聞中沒有財務數字」作為降低信心分數的理由。

處理步驟：

1. 讀取 `cleaned_news.sentiment_label`（由 Cleaner 在 `clean_node` 已計算完畢，**LLM 不重新判斷**），理解消息面情緒傾向；新聞的核心貢獻是**市場情緒訊號**，判斷依據為事件本身的性質（政策利多/利空、法人調降/調升評等、供應鏈正面/負面消息等）
2. 識別並標記情緒化動詞（例如：崩盤、起飛、噴出），保留事實陳述
3. 讀取 `preprocess_node` 轉換後的技術面敘述（如「股價低於月線 6%，處短線弱勢」），理解趨勢語義
4. 讀取籌碼面 `flow_label` 與累計數值，判斷法人方向與散戶籌碼結構
5. **讀取 `confidence_score` 與 `cross_validation_note`**（兩者皆由前置 `score_node` 的 rule-based Python 計算完畢，LLM 不得修改分數）；據此生成 `risks` / `summary` 文案
6. 產出結論、風險、`Technical_Signal`、`Institutional_Flow`

### System Prompt：矛盾檢查（Skeptic Mode）

`analyze_node` 的 system prompt 必須強制執行下列流程：

1. 讀取已提供的 `sentiment_label`（由 Cleaner 計算，**不重新判斷**），理解消息面情緒
2. 與 `technical_context` / `institutional_context` 逐項對照
3. 若訊號矛盾，必須標記衝突與風險
4. 僅輸出事實與邏輯推論，不得臆測或補造來源

### System Prompt：分維度強制分段（Session 8 新增）

> **設計動機**：目前 LLM 將三維資訊（新聞、技術、籌碼）揉雜在單一 `summary` 段落中，造成「資訊密度過高」且「推論過程像黑盒子」。分維度拆解不僅提升可讀性，更強化系統的可解釋性，使前端能顯示每個維度的「維度燈號」並讓使用者一眼看出哪個維度在拖累或支撐整體分數。

`analyze_node` 的 system prompt 必須要求 LLM **分段輸出四個獨立欄位**，禁止跨維度混寫：

```
請針對以下三個維度產出獨立的分析段落，禁止跨維度混寫：

[技術維度] tech_insight：
- 僅參考 technical_context 中的均線排列、RSI 位階、支撐壓力位
- 禁止提及法人買賣超、新聞事件等非技術資訊

[籌碼維度] inst_insight：
- 僅參考 institutional_context 中的三大法人買賣超與融資券動向
- 禁止提及均線數值、RSI、新聞事件等非籌碼資訊

[消息維度] news_insight：
- 僅參考 news_summary 中的事件性質與市場情緒傾向
- 禁止提及具體技術指標數值（如 RSI=62）

[綜合仲裁] final_verdict：
- 整合三維訊號，解釋為何這些訊號導向當前信心分數與策略
- 此段允許跨維度整合推論
```

**欄位對應**：LLM 必須分別輸出 `tech_insight`、`inst_insight`、`news_insight`、`final_verdict` 四個欄位（字串）；`summary` 欄位改由 `final_verdict` 填充（保留向後相容）。

**實作位置**：`langchain_analyzer.py` System Prompt + JSON output schema。

**衝突規則（v2，定案）**：

> 分數調整 = 各維度 lookup 分數加總 + 特殊情境 bonus/penalty。各維度基礎對照：`sentiment` positive/negative/neutral = +5/-5/0；`inst_flow` institutional_accumulation/distribution/retail_chasing/neutral/unknown = +7/-10/-8/0/0；`technical_signal` bullish/bearish/sideways = +5/-5/0。

| 情境 | 條件 | 分數調整（加總） | `cross_validation_note`（rule-based 固定字串） |
|------|------|----------|------------------------------------------------|
| 訊號共振 | `sentiment=positive` + `flow_label=institutional_accumulation` + `technical=bullish` | **+20**（+5+7+5 + bonus +3） | `"三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高"` |
| 利多出貨背離 | `sentiment=positive` + `flow_label=distribution`（技術中性） | **-12**（+5-10+0 + extra -7） | `"警示：市場消息正面但法人同步出貨，疑似趁消息出貨，建議保守觀察"` |
| 散戶追高危機 | `flow_label=retail_chasing`（其餘中性） | **-8**（-8） | `"散戶追高風險：融資餘額異常激增，法人同步減碼，籌碼結構偏不健康"` |
| 利空不跌 | `sentiment=negative` + `technical=bullish`（籌碼中性） | **0**（-5+5；僅觸發 note） | `"利空不跌訊號：股價守穩支撐且技術偏強，逆勢佈局機會，需觀察持續性"` |

> **架構決策**：`confidence_score` 從 **50** 開始（中性基準），clamp 至 [0, 100]。
> `cross_validation_note` 由 `score_node` 的 **純 rule-based Python** 產生固定字串，**不呼叫 LLM**。
> LLM 在 `analyze_node` 中可讀取 `confidence_score` 與 `cross_validation_note`，用於輸出 `risks` / `summary` 文案，但不得修改分數。
> `score_node` 位置：`preprocess → score → analyze → strategy → END`（在 LLM 分析前執行，讓 prompt 可讀取信心分數）。
> **`rsi14` 獨立 GraphState 欄位（T2-0）**：`rsi14: float | None` 除了嵌入 `technical_context` 敘事字串外，必須同時以獨立欄位寫入 `GraphState`，供 `strategy_node` 的 `calculate_action_plan_tag()` 直接讀取進行硬邏輯判斷（`rsi14 < 30` / `rsi14 > 70`）。不得從 narrative 字串反解數值。

> 註：若做正式上線，建議將「內部推理」與「對外輸出」分離，避免過度暴露模型中間推理內容。

### Tool Use（強制最小集合）

> ⚠️ **強制規範**：以下計算工具**必須**呼叫，禁止 LLM 自行估算任何數值。

- **技術指標計算工具** `calculate_technical_indicators(symbol, window)`
  - 輸入：yfinance OHLCV DataFrame
  - 輸出：`{ ma5, ma20, ma60, bias_ma20, rsi14, volume_change_pct, high_20d, low_20d, support_20d, resistance_20d }`
  - 實作：Pandas rolling mean + RSI Wilder 平滑法

- **乖離率工具** `calculate_bias(close, ma)`
  - 公式：`(close - ma) / ma × 100`

- **技術位階工具** `calculate_price_levels(symbol, window=20)`
  - 輸出：`{ high_20d, low_20d, support_20d, resistance_20d }`
  - 用途：提供入手區間與防守底線的硬數值，禁止 LLM 臆測
  - **位階敘事判斷邊界**（`_price_level_narrative` 規範）：
    - 「接近支撐位」：`close <= support_20d * 1.02`（含等於，2% 緩衝）
    - 「接近壓力位」：`close >= resistance_20d * 0.98`（含等於，2% 緩衝）
    - 兩者皆不命中：輸出「現價處於支撐與壓力之間，位階中立」
    - 使用 `<=` / `>=` 確保收盤剛好等於支撐/壓力位時行為確定（算「有撐/有壓」，不落入中立）

- **本益比位階工具** `estimate_pe_percentile(symbol, pe)`
  - 與歷史 PE 分佈比較，回傳百分位

- **簡易成長率換算** `calculate_growth_rate(current, previous)`
  - YoY / MoM 標準化計算

- **法人籌碼查詢工具** `fetch_institutional_flow(symbol, days)`
  - 輸入：股票代碼、回溯天數
  - 視窗建議：`days=5` 用於資料源 smoke test；正式分析至少 `days>=20`，建議 `days=60`
  - 輸出：`{ foreign_net_cumulative, trust_net_cumulative, margin_balance_delta }`

### 輸出結構（升級後）

```json
{
  "summary": "事實型摘要（去情緒化後的核心資訊）",
  "sentiment_label": "positive | negative | neutral",
  "confidence_score": 78,
  "technical_signal": "bullish | bearish | sideways",
  "institutional_flow": "institutional_accumulation | retail_chasing | distribution | neutral",
  "strategy_type": "short_term | mid_term | defensive_wait",
  "entry_zone": "892.0-905.0（support_20d ~ MA20）",
  "stop_loss": "865.4（近20日低點892.2 × 0.97，或跌破 MA60=870.0）",
  "holding_period": "1-2 週 | 1-3 個月",
  "action_plan": {
    "action": "觀望 | 分批佈局 | 持股續抱",
    "target_zone": "800-820",
    "defense_line": "780",
    "momentum_expectation": "強（法人集結中）"
  },
  "cross_validation_note": "外資連買 3 日，與新聞利多訊號一致，信心分數維持高位",
  "risks": ["RSI 接近超買區間 (>70)，短線需注意回測"],
  "data_sources": ["google-news-rss", "yfinance", "twse-openapi"],
  "tech_insight": "均線多頭排列，RSI 62 位於健康動能區，短線無超買疑慮。",
  "inst_insight": "外資近 5 日累計買超 12,500 張，籌碼持續沉澱，機構資金流向偏多。",
  "news_insight": "法說會利多消息帶動市場情緒正面，事件時效性已驗證（日期明確）。",
  "final_verdict": "三維訊號共振：技術面健康、籌碼面偏多、消息面正面，信心分數 78 反映訊號一致性高。"
}
```

> **分維度分析欄位說明（Session 8 新增）**：
> - `tech_insight`：技術面獨立分析段落，聚焦均線排列、RSI 位階、支撐壓力解讀，**不混入籌碼或消息面**
> - `inst_insight`：籌碼面獨立分析段落，聚焦三大法人買賣超與融資券對作格局，**不混入技術或消息面**
> - `news_insight`：消息面獨立分析段落，聚焦市場情緒、事件性質與時效性驗證，**不混入數值指標**
> - `final_verdict`：綜合仲裁段落，解釋三維訊號如何導向當前信心分數與策略；此段允許跨維度整合推論
> - 四個欄位均由 LLM 在 `analyze_node` 生成，但 LLM **不得修改** `confidence_score`（由前置 `score_node` rule-based 計算）

### JSON Schema 範例（AnalyzeResponse 重點欄位）

```json
{
  "type": "object",
  "required": [
    "summary",
    "sentiment_label",
    "confidence_score",
    "technical_signal",
    "institutional_flow",
    "strategy_type",
    "entry_zone",
    "stop_loss",
    "holding_period",
    "cross_validation_note",
    "risks",
    "data_sources"
  ],
  "properties": {
    "summary": { "type": "string", "minLength": 1 },
    "sentiment_label": { "enum": ["positive", "negative", "neutral"] },
    "confidence_score": { "type": "integer", "minimum": 0, "maximum": 100 },
    "technical_signal": { "enum": ["bullish", "bearish", "sideways"] },
    "institutional_flow": {
      "enum": ["institutional_accumulation", "retail_chasing", "distribution", "neutral", "unknown"]
    },
    "strategy_type": { "enum": ["short_term", "mid_term", "defensive_wait"] },
    "entry_zone": {
      "type": "string",
      "pattern": "^\\d+(\\.\\d+)?-\\d+(\\.\\d+)?（.+）$"
    },
    "stop_loss": {
      "type": "string",
      "pattern": "^\\d+(\\.\\d+)?（.+）$"
    },
    "holding_period": {
      "type": "string",
      "minLength": 2,
      "pattern": "\\d",
      "description": "必須包含具體數字範圍（如「7-10 交易日」、「4-8 週」），不接受純文字如「短期」"
    },
    "action_plan": {
      "type": ["object", "null"],
      "description": "選填（optional）。strategy 計算失敗或資料不足時為 null",
      "properties": {
        "action": { "type": "string" },
        "target_zone": { "type": "string" },
        "defense_line": { "type": "string" },
        "momentum_expectation": { "type": "string" }
      }
    },
    "cross_validation_note": { "type": "string", "minLength": 1 },
    "risks": { "type": "array", "items": { "type": "string" } },
    "data_sources": { "type": "array", "items": { "type": "string" } },
    "news_display_items": {
      "type": "array",
      "maxItems": 5,
      "description": "最多 5 筆近期新聞，供前端顯示，每筆直接取 RSS 原始欄位",
      "items": {
        "type": "object",
        "properties": {
          "title": { "type": "string" },
          "date": { "type": ["string", "null"] },
          "source_url": { "type": ["string", "null"] }
        }
      }
    },
    "tech_insight": {
      "type": ["string", "null"],
      "description": "技術面獨立分析段落（均線排列、RSI 位階、支撐壓力）；禁止混入籌碼或消息面資訊"
    },
    "inst_insight": {
      "type": ["string", "null"],
      "description": "籌碼面獨立分析段落（三大法人買賣超、融資券對作）；禁止混入技術或消息面資訊"
    },
    "news_insight": {
      "type": ["string", "null"],
      "description": "消息面獨立分析段落（市場情緒、事件性質、時效性）；禁止混入技術指標數值"
    },
    "final_verdict": {
      "type": ["string", "null"],
      "description": "綜合仲裁段落：解釋三維訊號如何導向當前信心分數與策略；允許跨維度整合推論"
    }
  }
}
```

### 新聞摘要品質門檻（News Summary Quality Gate）

> 目的：避免新聞摘要出現「標題其實是時間戳」或「日期未知但未標示可信度」等低可用輸出。

- `cleaned_news.title` 不得為純時間字串、純 URL、純來源代碼（例如僅 `Wed, 04 Mar ...`）
- `cleaned_news.date` 應優先保留來源時間（ISO 8601 或 RFC 2822）；無法解析時允許 `unknown`，但必須標記品質旗標
- `cleaned_news.mentioned_numbers` 需過濾與市場分析無關之雜訊數字（例如純日期碎片）
- 新增 `cleaned_news_quality`（或同義欄位）以回傳 `quality_score`（0-100）與 `quality_flags`（如 `TITLE_LOW_QUALITY`、`DATE_UNKNOWN`、`NO_FINANCIAL_NUMBERS`）
- 當品質旗標命中時，前端需以「摘要品質受限」提示，不得當成高可信重點摘要

**`DATE_UNKNOWN` 信心分數懲罰規則（rule-based，在 `score_node` 執行）**：

- 當 `quality_flags` 含有 `DATE_UNKNOWN` 時，`signal_confidence` 額外 **-3**
- 理由：日期未知的新聞時效性無法驗證，可能為過期資訊，應主動降低訊號可信度
- 同時在 `cross_validation_note` 末尾追加固定字串：`「（注意：新聞日期不明，時效性未驗證）」`
- 此懲罰不影響 `data_confidence`（日期格式問題不等於資料維度未取得）
- 執行位置：`score_node` 的 rule-based Python，在各維度加總後、clamp 前套用

### 新聞資料拆分設計（News Display Split，新增）

> 問題根源：`cleaned_news` 同時服務兩個目的（LLM pipeline 消費 + 前端顯示），造成標題/日期欄位因 LLM 清潔品質而呈現不可讀的時間戳或結構化前綴。

**設計決策：**
- `cleaned_news`（保留）：專供 LLM pipeline 消費，含 `sentiment_label`、`mentioned_numbers`
- `news_display_items`（新增）：**陣列**，專供前端顯示近期新聞列表，每筆從 `raw_news_items` 直接取 RSS 原始欄位，不經 LLM 清潔

**`news_display_items` 欄位規格：**

```json
[
  {
    "title": "台積電 Q1 法說會重點整理",
    "date": "2026-03-05",
    "source_url": "https://news.example.com/..."
  },
  {
    "title": "外資連三日買超台積電",
    "date": "2026-03-04",
    "source_url": "https://news.example.com/..."
  }
]
```

| 欄位 | 來源 | 說明 |
|------|------|------|
| `title` | `raw_news_items[i].title` | RSS 原始標題，不經 LLM 清潔 |
| `date` | `raw_news_items[i].pub_date` via `QualityGate.normalize_date` | RFC 2822 → ISO 8601；`unknown` → `null` |
| `source_url` | `raw_news_items[i].url` | RSS 原始連結，供使用者點擊跳轉 |

> **筆數建議**：預設輸出最多 5 筆，依 `raw_news_items` 實際數量而定，確保前端有足夠近期新聞可瀏覽。

**產出節點：** `quality_gate_node`（在 `clean_node` 之後執行，`raw_news_items` 仍在 state 中）

**前端渲染規則：**
- 新聞標題、日期、「查看原文」連結 → 讀 `news_display_items`（每筆渲染為可點擊連結，`source_url` 以新分頁開啟）
- 情緒 badge → 讀 `cleaned_news.sentiment_label`（僅標示在第一筆或整體情緒旗標）
- `mentioned_numbers` chips → 移除（對使用者無顯示價值）
- 品質受限提示 → 讀 `cleaned_news_quality`

**`fetch_news_node` 結構化輸出（同步修正）：**

`news_content` 改用明確欄位標籤格式，避免 LLM 把時間戳誤識為標題：

```
日期: Mon, 03 Mar 2026 08:00:00 GMT
標題: 台積電 2 月營收年增 20%
摘要: 台積電公佈 2 月營收，年增 20%，優於市場預期。
```

> **`Technical_Signal` 定義（v2，CS-5，2026-03-05 升級）**
> 改為 `derive_technical_score()` 加權模型，舊多 AND 條件已廢除：
> - `bullish`（多）：`derive_technical_score()` 回傳 `score >= 60`（RSI / BIAS / MA 排列三維加權總分偏多）
> - `bearish`（空）：`score <= 40`（三維加權總分偏空）
> - `sideways`（盤整）：`40 < score < 60`（訊號不明確）
> - **`unknown` 不存在**：資料不足時 `derive_technical_score()` 回傳 50，降級為 `sideways`；`technical_signal` 不輸出 `unknown`，`data_confidence` 計算亦不需判斷此值
>
> `derive_technical_score()` 詳見 `analysis/confidence_scorer.py`：RSI ≥ 50 → +1、BIAS 0~5% → +1（>10% 或 <-10% → -1）、close > ma5 > ma20 → +1（ma5 < ma20 → -1）；總分 [-3, +3] 映射至 [30, 70]。
> 因 raw 為整數，映射後的值不為整數（raw=+1 → 56.7、raw=+2 → 63.3），實際觸發邊界為：raw ≥ +2（mapped ≈ 63）→ bullish，raw ≤ -2（mapped ≈ 37）→ bearish，raw = ±1（mapped ≈ 57 / 43）→ sideways。文件閾值（60 / 40）為保守設計，不存在剛好等於 60 / 40 的情境。

> **`Institutional_Flow` 定義**
> - `institutional_accumulation`（大戶吸籌）：三大法人合計買超，融券增加（放空減少）
> - `retail_chasing`（散戶追高）：融資大增，法人同步出貨
> - `distribution`（主力出貨）：法人連賣，成交量放大
> - `neutral`：訊號不明確

> **`Strategy_Type`（新增）**
> - `short_term`：短線策略（1-2 週），典型組合為「新聞利多 + RSI 超賣反彈」
> - `mid_term`：中線策略（1-3 個月），典型組合為「法人持續吸籌 + 均線多頭排列」
> - `defensive_wait`：訊號衝突或風險過高時採觀望

> **策略價格規則（新增，rule-based）**
> - `entry_zone`：若 BIAS 過高，建議「拉回 MA20 入手」；否則以 `support_20d ~ MA20` 組區間，輸出必須為具體數值區間（例：`892.0-905.0`）
> - `stop_loss`：預設採 `近20日低點 × 0.97`，並加註 `破 MA60 停損`，輸出必須為具體價位（例：`865.4`）
> - `holding_period`：依 `strategy_type` 輸出可執行時間窗（例：`7-10 交易日`、`4-8 週`），不可只寫「短期/中期」
> - 上述欄位必須由 Python 工具先算出硬數值，再交由 LLM 做文字說明

---

## 4. 前端展示需求（React + Tailwind）

目標：製作「AI 決策儀表板」，重點是可解釋與可比較。

### 4.1 必要元件

1. **股票代碼輸入框**
   - MVP 入口（例如輸入 2330.TW）

2. **信心指數元件**
   - 圓形進度條顯示 AI confidence（0~100）

3. **近期新聞列表**
   - 顯示最多 5 筆近期新聞，資料來自 `news_display_items`
   - 每筆顯示：標題（可點擊連結，開新分頁）、發布日期
   - 整體情緒 badge（positive / negative / neutral）顯示於列表標題旁
   - 標示「以上新聞為市場情緒參考，財報數字請參閱公開資訊觀測站」

4. **雜訊過濾對比視窗**
   - 左：AI 消息面情緒摘要（`cleaned_news.sentiment_label` + `summary`）
   - 右：技術面與籌碼面數據條列

5. **分析路徑圖（流程事件）**
   - 例：「抓取新聞中 → 抽取數值完成 → 驗證歷史資料完成」

6. **戰術行動（Action Plan）卡片**
  - 操作方向：`觀望 / 分批佈局 / 持股續抱`
  - 建議區間：由 `entry_zone` + `support_20d` / `resistance_20d` 組成
  - 防守底線：`stop_loss`（例如「近 20 日低點 -3%」或「破 MA60」）
  - 預期動能：依 `institutional_flow` 與 `technical_signal` 共振結果顯示

7. **分維度分析卡片（Session 8 新增）**
   - 「LLM 分析報告」區塊改為三張獨立小卡 + 一張綜合仲裁卡，取代單一大文字方塊
   - **技術面卡片**：顯示 `tech_insight` 內容，標題旁附維度燈號（技術面訊號 bullish/bearish/sideways 對應 🟢/🔴/🔵）
   - **籌碼面卡片**：顯示 `inst_insight` 內容，標題旁附維度燈號（institutional_flow 對應 🟢/🔴/🔵）；卡片下方附原始數據（如外資買超張數）增加說服力
   - **消息面卡片**：顯示 `news_insight` 內容，標題旁附整體情緒 badge（positive/negative/neutral）
   - **綜合仲裁卡片**：顯示 `final_verdict` 內容，為全寬卡片，放置於三張小卡下方
   - 使用「手風琴（Accordion）」或「標籤頁（Tabs）」切換三個維度（前端技術選擇）
   - 視覺目標：使用者一眼看出哪個維度在「拖累」整體分數、哪個維度在「支撐」策略

### 4.2 UX 要點

- 顯示資料來源與時間戳
- 明確標示「推論」與「事實」區塊
- 長流程分析需有進度狀態（loading / step logs）

**紅綠燈標籤定義（新增）**：
- 🟢 機會：`rsi < 30` 且 `institutional_flow = institutional_accumulation` 且 `confidence_score > 70`
- 🔴 過熱/風險：`rsi > 70` 且 `institutional_flow = distribution`
- 🔵 中性：其餘狀況

> 上述燈號判斷由 Python rule-based 產生，前端僅做顯示。

**`calculate_action_plan_tag()` 實作規範**：
```python
def calculate_action_plan_tag(
    rsi14: float | None,
    flow_label: str | None,
    confidence_score: int | None,
) -> str:  # "opportunity" | "overheated" | "neutral"
```
- 任一輸入為 `None` → 直接回傳 `"neutral"`（安全降級）
- 🟢 `opportunity`：`rsi14 < 30` 且 `flow_label == "institutional_accumulation"` 且 `confidence_score > 70`
- 🔴 `overheated`：`rsi14 > 70` 且 `flow_label == "distribution"`
- 🔵 `neutral`：其餘情況
- 此函式為純 rule-based Python，**不呼叫 LLM**；建議放在 `analysis/strategy_generator.py`

**`generate_action_plan()` 實作規範**：
- 獨立於 `generate_strategy()` 的 rule-based 函式，輸出 `action_plan` dict
- 輸入：`strategy_type`、`entry_zone`、`stop_loss`、`flow_label`、`confidence_score`
- 輸出：`{ action, target_zone, defense_line, momentum_expectation }`
- `strategy_type == "defensive_wait"` → `action = "觀望"`
- **不呼叫 LLM**；建議放在 `analysis/strategy_generator.py`

---

## 5. 建議技術棧

- **Backend**：Python 3.10+, FastAPI, LangChain, LangGraph
- **數據處理**：
  - `yfinance`：拉取 OHLCV 歷史行情、即時報價
  - `pandas`：計算技術指標（rolling mean、pct_change、自定義 RSI）
  - `ta` / `pandas-ta`（可選）：封裝常用技術分析計算，避免重複造輪
  - `twstock`（已評估，功能已由 FinMind + TWSE OpenAPI 三軌策略取代，不採用）
- **Crawler**：Firecrawl / Browserbase（擇一先導入）
- **Frontend**：React, Tailwind CSS, TypeScript
- **Storage（可選）**：PostgreSQL / SQLite（MVP 可先檔案化）
- **Queue（可選）**：Celery / RQ（若後續要批次分析）

### 5.1 Pandas 進階應用規範

```python
import pandas as pd
import yfinance as yf

def calculate_technical_indicators(symbol: str, period: str = "3mo") -> dict:
    """
    從 yfinance 拉取 OHLCV，以 Pandas 計算技術指標。
    所有數值均由此函式計算後返回，禁止 LLM 自行推算。
    """
    df = yf.download(symbol, period=period, auto_adjust=True)
    close = df["Close"]

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    bias_ma20 = (close.iloc[-1] - ma20) / ma20 * 100

    # RSI (Wilder 平滑法)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rsi14 = (100 - 100 / (1 + gain / loss)).iloc[-1]

    vol = df["Volume"]
    vol_ma5 = vol.rolling(5).mean().iloc[-1]
    volume_change_pct = (vol.iloc[-1] - vol_ma5) / vol_ma5 * 100

    high_20d = df["High"].rolling(20).max().iloc[-1]
    low_20d = df["Low"].rolling(20).min().iloc[-1]

    # MVP 版位階：先以近 20 日高低點作為支撐/壓力位基準
    support_20d = low_20d
    resistance_20d = high_20d

    return {
        "ma5": round(float(ma5), 2),
        "ma20": round(float(ma20), 2),
        "ma60": round(float(ma60), 2),
        "bias_ma20": round(float(bias_ma20), 2),
        "rsi14": round(float(rsi14), 2),
        "volume_change_pct": round(float(volume_change_pct), 2),
        "high_20d": round(float(high_20d), 2),
        "low_20d": round(float(low_20d), 2),
        "support_20d": round(float(support_20d), 2),
        "resistance_20d": round(float(resistance_20d), 2),
    }
```

---

## 6. MVP 里程碑

### Phase 1（已部分完成）

- yfinance 抓取股票快照
- 新聞清潔 JSON（date/title/numbers/sentiment）

### Phase 2（已完成）

- 導入 LangGraph，建立「資料不足→補抓」回圈
- 加入新聞 RSS 抓取
- 加入計算工具（本益比位階、乖離率）

### Phase 3（已完成：前端初步串接）

- React 輸入框 + 分析結果頁
- 信心指數與雜訊對比元件
- Agent 分析路徑可視化

### Phase 4（下一步：深度分析升級）

- 技術術語語義化翻譯層（RSI/BIAS/MA/Institutional Flow）
- Action Plan 強制輸出具體價位（entry/stop/holding）
- 籌碼資料雙軌穩定化（FinMind Primary + TWSE OpenAPI Fallback）

### Phase 5（Session 8：分析敘事結構化）

- `AnalysisDetail` 新增 `tech_insight` / `inst_insight` / `news_insight` / `final_verdict` 四欄位
- `langchain_analyzer.py` JSON 輸出要求更新：強制分段輸出，禁止跨維度混寫
- 前端 UI 改版：「LLM 分析報告」改為分欄式三維小卡 + 綜合仲裁卡配置

---

## 7. 驗收標準（Definition of Done）

- 能輸入股票代碼並觸發完整分析流程
- 當資料不足時，系統會自動補抓後再分析
- **技術面**：必須包含由 Python 函式計算後的 MA5/20/60、BIAS、RSI14、成交量變化數值
- **語義化翻譯層**：必須將 RSI、BIAS、MA、Institutional Flow 映射為直白中文敘事（由 `preprocess_node` rule-based 產生）
- **技術位階**：必須包含 `high_20d`、`low_20d`、`support_20d`、`resistance_20d`
- **籌碼面**：必須採 `FinMindProvider`（Primary）+ `TwseOpenApiProvider`（Fallback）雙軌策略，並包含三大法人合計買賣超方向及融資融券變化
- **多維交叉驗證**：當新聞訊號與法人動向背離時，系統須在 `cross_validation_note` 中明確標記警示
- **新聞摘要品質**：
  - `cleaned_news.title` 必須是可讀的事件語句，不可為純時間戳/純 URL
  - `cleaned_news.date` 若為 `unknown`，必須伴隨品質旗標（如 `DATE_UNKNOWN`）
  - `mentioned_numbers` 需經過財經語意過濾，避免日期碎片主導摘要
  - 當品質低於門檻（例如 `quality_score < 60`）時，前端需顯示「摘要品質受限」
- 輸出必須包含：
  - `summary`：去情緒化事實型摘要
  - `sentiment_label`：positive / negative / neutral
  - `technical_signal`：bullish / bearish / sideways（必填）
  - `institutional_flow`：institutional_accumulation / retail_chasing / distribution / neutral（必填）
  - `strategy_type`：short_term / mid_term / defensive_wait（必填）
  - `entry_zone`（必填，具體價格區間）
  - `stop_loss`（必填，具體停損價位，例：近20日低點 -3%）
  - `holding_period`（必填，具體時間窗）
  - `confidence_score`：0–100，反映三維訊號一致性
  - `cross_validation_note`：說明三維訊號的交叉驗證結論
  - `risks`：風險提示列表
  - `data_sources`：資料來源列表
- 所有數值指標**必須**可追溯至 yfinance / TWSE 原始資料，不得由 LLM 直接生成
- **分維度分析**：LLM 輸出必須包含 `tech_insight`（技術面）、`inst_insight`（籌碼面）、`news_insight`（消息面）、`final_verdict`（綜合仲裁）四個獨立段落；各維度禁止跨維度混寫
- 前端可視化顯示分析過程與最終結論（含三維訊號燈號）

---

## 8. 風險與注意事項

- 資料來源授權與抓取頻率限制（避免封 IP）
- 新聞內容版權與儲存策略
- 模型幻覺風險：需以可驗證資料回填
- 指標定義需版本化，避免不同批次結論不可比
