# AI Stock Sentinel 技術架構需求文件

> 日期：2026-03-03  
> 狀態：Draft v2  
> 目的：將產品需求大綱轉為可落地的工程實作藍圖  
> 更新摘要：分析維度從單純「新聞去雜訊」擴展為「全方位數據偵察」

## 1. 目標與方向

AI Stock Sentinel 採用 TypeScript + Python 混合架構，核心目標為：

- 建立可循環補資料的多 Agent 流程
- **全方位數據偵察**：整合新聞、技術指標、籌碼三大維度，進行多維交叉驗證，降低單一訊號誤判風險
- 嚴格落實 **Tool Use（工具化計算）**：嚴禁 LLM 盲猜數值，所有技術指標與籌碼數據必須透過 Python 函式計算後，再交由 LLM 進行定性分析
- 將分析過程透明化，前端可視化呈現 AI 決策路徑

### 1.1 三大分析維度

| 維度 | 說明 | 資料來源 |
|------|------|----------|
| **消息面 (News)** | 去情緒化後的事實型新聞摘要 | Google News RSS、財經媒體 RSS |
| **技術面 (Technical)** | MA5/20/60 均線、乖離率 (BIAS)、RSI、成交量變化 | yfinance + Pandas 計算 |
| **籌碼面 (Institutional)** | 三大法人（外資、投信、自營商）買賣超、融資融券消長 | 公開資訊觀測站 / 專用 API |

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
  - 責任：事實萃取、情緒字眼標記、指標計算、結論生成
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

- **技術面指標**（由 `yfinance` 拉取 OHLCV 後，以 **Pandas** 計算）
  - `MA5`、`MA20`、`MA60`：收盤價簡單移動平均
  - `BIAS`：乖離率，公式：`(Close - MAn) / MAn × 100`
  - `RSI14`：14 日相對強弱指標，使用 Wilder 平滑法
  - `Volume_Change`：今日成交量相較 MA5_Volume 的百分比變化

  > ⚠️ **嚴格規範**：所有數值必須由 Python 函式（pandas / ta-lib）實際計算，**禁止** LLM 自行推算或估計。

- **籌碼面（法人動向）**
  - 三大法人買賣超：外資（FINI）、投信（SITC）、自營商（Dealer）
  - 融資餘額變化（Margin Balance Delta）
  - 融券餘額變化（Short Balance Delta）
  - 資料源：公開資訊觀測站 OpenAPI 或 `twstock` 庫

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

**典型衝突情境（訊號背離）**：

| 情境名稱 | 描述 | 處理邏輯 |
|----------|------|----------|
| 利多不漲（Bullish Divergence） | 新聞極度樂觀（如營收創高）＋ 外資/投信連續大賣 ＋ 技術面高檔長上影線 | 信心分數大幅調降；`cross_validation_note` 標記「基本面利多疑已反映，法人趁利多出貨，建議轉保守觀察」 |
| 利空不跌（Bearish Divergence） | 新聞負面 ＋ 股價守穩關鍵支撐 ＋ 大法人低調承接 | 信心分數小幅上調；標記「逆勢佈局信號，需觀察是否持續」 |
| 訊號共振（Alignment） | 三維方向一致（例：利多 ＋ 外資連買 ＋ RSI 健康） | 信心分數正常或上調；標記「多方共振，訊號可信度高」 |

**信心分數調整規則（rule-based，非 LLM 計算）**：

```python
def adjust_confidence_by_divergence(
    base_score: int,
    news_sentiment: str,       # "positive" | "negative" | "neutral"
    inst_flow: str,            # "accumulation" | "distribution" | "neutral"
    technical_signal: str      # "bullish" | "bearish" | "sideways"
) -> tuple[int, str]:
    """
    根據三維訊號一致性調整信心分數。
    回傳 (adjusted_score, cross_validation_note)。
    直接由 Python 計算，不依賴 LLM。
    """
    note = ""
    adjustment = 0

    # 訊號共振：三維方向一致 → 加分
    if news_sentiment == "positive" and inst_flow == "accumulation" and technical_signal == "bullish":
        adjustment = +15
        note = "三維訊號共振（利多 + 法人買超 + 技術多頭），信心度偏高"

    # 利多不漲背離：新聞正面但法人出貨 → 大幅降分
    elif news_sentiment == "positive" and inst_flow == "distribution":
        adjustment = -20
        note = "警示：基本面利多但法人同步出貨，疑似趁消息出貨，建議保守觀察"

    # 散戶追高危機：融資暴增 + 法人出貨
    elif inst_flow == "retail_chasing":
        adjustment = -15
        note = "散戶追高風險：融資餘額異常激增，法人同步減碼，籌碼結構偏不健康"

    adjusted = max(0, min(100, base_score + adjustment))
    return adjusted, note
```

---

### 分析模式二：技術指標定性化（Quant to Qual）

> 在呼叫 LLM 分析前，必須先透過 `preprocess_node` 將數值轉換為「敘事背景」。

**設計動機**：LLM 看到 `ma20: 850, close: 800` 無法可靠判斷意涵；但看到「股價低於月線 6%，處短線弱勢，存在超賣反彈需求」則能立即理解語義進行推理。

**架構位置**：在 LangGraph 流程中，`calculate_indicators_node` → **`preprocess_node`（新增）** → `analyze_node`

**ContextGenerator（建議命名）**：
- `generate_technical_context(df_price, inst_data)` 作為 `preprocess_node` 的核心函式
- 同時產生 `technical_context` 與 `institutional_context`
- 由 Python rule-based 生成「敘事背景」，禁止呼叫 LLM

```python
def quantify_to_narrative(technical: dict) -> str:
    """
    將技術指標數值轉換為 LLM 可直接理解的敘事描述。
    純 rule-based，100% 可測試，不依賴 LLM。
    """
    lines = []

    # 乖離率（BIAS）
    bias = technical.get("bias_ma20", 0)
    if bias < -5:
        lines.append(f"股價低於月線（MA20）{abs(bias):.1f}%，處短線弱勢格局，乖離率偏大，存在超賣反彈期待")
    elif bias > 8:
        lines.append(f"股價高於月線（MA20）{bias:.1f}%，乖離率偏高，注意短線回調風險")
    else:
        lines.append(f"股價與月線（MA20）乖離率 {bias:.1f}%，屬正常區間")

    # RSI
    rsi = technical.get("rsi14", 50)
    if rsi > 70:
        lines.append(f"RSI14 為 {rsi:.1f}，進入超買區間（>70），短線動能可能趨緩")
    elif rsi < 30:
        lines.append(f"RSI14 為 {rsi:.1f}，進入超賣區間（<30），短線存在反彈訊號")
    else:
        lines.append(f"RSI14 為 {rsi:.1f}，處於中性區間（30~70）")

    # 均線多空排列
    ma5 = technical.get("ma5", 0)
    ma20 = technical.get("ma20", 0)
    ma60 = technical.get("ma60", 0)
    if ma5 > ma20 > ma60:
        lines.append("均線呈多頭排列（MA5 > MA20 > MA60），中長線趨勢向上")
    elif ma5 < ma20 < ma60:
        lines.append("均線呈空頭排列（MA5 < MA20 < MA60），中長線趨勢向下")
    else:
        lines.append("均線糾結中，趨勢方向尚不明確")

    # 成交量
    vol_chg = technical.get("volume_change_pct", 0)
    if vol_chg > 50:
        lines.append(f"今日成交量較五日均量放大 {vol_chg:.0f}%，量能顯著放大，需留意是否為主力異動")
    elif vol_chg < -30:
        lines.append(f"今日成交量較五日均量萎縮 {abs(vol_chg):.0f}%，市場觀望情緒濃厚")

    return "\n".join(lines)
```

---

### 分析模式三：籌碼歸屬分析（Institutional Flow Profiling）

> 教 AI 識別「誰」在影響股價，並判斷籌碼結構健康度。

**⚠️ 前置條件**：此模式依賴三大法人每日買賣超資料（`foreign_net`、`trust_net`、`dealer_net`）與融資融券餘額，**需先確認資料源可用性**（建議 `twstock` 或公開資訊觀測站 OpenAPI），確認後方可實作。

### 籌碼資料源策略（Provider Abstraction）

> 原則：不得依賴單一資料源；主來源不可用時必須自動降級，避免分析流程中斷。

**Provider 介面（建議）**：

```python
from typing import Protocol

class InstitutionalFlowProvider(Protocol):
  def fetch_daily_flow(self, symbol: str, days: int) -> dict:
    ...
```

**Provider 優先序（v1）**：
1. `FinMindProvider`（Primary）：欄位完整、API 友善
2. `TwstockProvider` / `TwseOpenApiProvider`（Fallback）：Primary 限流或失敗時自動切換

**最小可用驗收（MVP）**：
- 可對 `2330.TW`（映射 `2330`）抓到近 5 日：`foreign_buy`、`investment_trust_buy`、`dealer_buy`、`margin_delta`
- 失敗時回傳可追蹤錯誤碼（`INSTITUTIONAL_FETCH_ERROR`）且不中斷主流程

**籌碼集中度判斷模型**：

| 指標 | 資料來源 | 狀態判斷 | AI 解讀方向 |
|------|----------|----------|-------------|
| 三大法人合計連續買超（≥3 日） | TWSE OpenAPI | `institutional_accumulation` | 強力多頭支撐，信心分數 +15~+20 |
| 外資單日大賣超（超過近 5 日均值 2 倍） | TWSE OpenAPI | `distribution` risk | 主力出貨警示，信心分數 −15 |
| 融資餘額單日增幅 >5%（相對近 10 日均值） | TWSE OpenAPI | `retail_chasing` risk | 散戶追高，籌碼混亂，信心分數 −10 |
| 融資餘額持續下降 ＋ 股價上漲 | TWSE OpenAPI | 籌碼沉澱 | 健康換手，`institutional_accumulation` 加成 |
| 成交量 > 月均量 50% ＋ 股價平漲 | yfinance | 量價平量增 | 底部換手或有人墊高成本，需搭配法人方向判斷 |

**`fetch_institutional_flow` 工具輸出規格**：

```json
{
  "symbol": "2330.TW",
  "period_days": 5,
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

處理步驟：

1. 提取新聞中所有提及數值（營收、EPS、毛利率、目標價、漲跌幅等）
2. 識別並標記情緒化動詞（例如：崩盤、起飛、噴出），保留事實陳述
3. 讀取 `preprocess_node` 轉換後的技術面敘述（如「股價低於月線 6%，處短線弱勢」），理解趨勢語義
4. 讀取籌碼面 `flow_label` 與累計數值，判斷法人方向與散戶籌碼結構
5. **執行多維交叉驗證**，但信心分數調整由 `adjust_confidence_by_divergence` Python 函式完成，LLM 只負責生成 `cross_validation_note` 的文字解釋
6. 產出結論、風險、`Technical_Signal`、`Institutional_Flow`（信心分數已由前一步 rule-based 計算完畢）

### System Prompt：矛盾檢查（Skeptic Mode）

`analyze_node` 的 system prompt 必須強制執行下列流程：

1. 從新聞提取情感標籤（Sentiment）
2. 與 `technical_context` / `institutional_context` 逐項對照
3. 若訊號矛盾，必須標記衝突與風險
4. 僅輸出事實與邏輯推論，不得臆測或補造來源

**衝突規則（v1）**：
- 若 `[新聞=利多]` 且 `[法人=大賣]`：`confidence_score -30`，註記「警惕利多出貨」
- 若 `[新聞=利空]` 且 `[股價=不跌反漲]`：`confidence_score +10`，註記「利空不跌，籌碼轉強」

> 分數運算由 Python `score_node` 負責；LLM 僅負責生成可讀解釋（`cross_validation_note` / `risks`）。

> 註：若做正式上線，建議將「內部推理」與「對外輸出」分離，避免過度暴露模型中間推理內容。

### Tool Use（強制最小集合）

> ⚠️ **強制規範**：以下計算工具**必須**呼叫，禁止 LLM 自行估算任何數值。

- **技術指標計算工具** `calculate_technical_indicators(symbol, window)`
  - 輸入：yfinance OHLCV DataFrame
  - 輸出：`{ ma5, ma20, ma60, bias_ma20, rsi14, volume_change_pct }`
  - 實作：Pandas rolling mean + RSI Wilder 平滑法

- **乖離率工具** `calculate_bias(close, ma)`
  - 公式：`(close - ma) / ma × 100`

- **本益比位階工具** `estimate_pe_percentile(symbol, pe)`
  - 與歷史 PE 分佈比較，回傳百分位

- **簡易成長率換算** `calculate_growth_rate(current, previous)`
  - YoY / MoM 標準化計算

- **法人籌碼查詢工具** `fetch_institutional_flow(symbol, days)`
  - 輸入：股票代碼、回溯天數
  - 輸出：`{ foreign_net_cumulative, trust_net_cumulative, margin_balance_delta }`

### 輸出結構（升級後）

```json
{
  "summary": "事實型摘要（去情緒化後的核心資訊）",
  "sentiment_label": "positive | negative | neutral",
  "confidence_score": 78,
  "technical_signal": "bullish | bearish | sideways",
  "institutional_flow": "institutional_accumulation | retail_chasing | distribution | neutral",
  "cross_validation_note": "外資連買 3 日，與新聞利多訊號一致，信心分數維持高位",
  "risks": ["RSI 接近超買區間 (>70)，短線需注意回測"],
  "data_sources": ["google-news-rss", "yfinance", "twse-openapi"]
}
```

> **`Technical_Signal` 定義**
> - `bullish`（多）：均線多頭排列（MA5 > MA20 > MA60）且 RSI 50~70
> - `bearish`（空）：均線空頭排列或 RSI < 30
> - `sideways`（盤整）：均線糾結或 RSI 40~60 無明確方向

> **`Institutional_Flow` 定義**
> - `institutional_accumulation`（大戶吸籌）：三大法人合計買超，融券增加（放空減少）
> - `retail_chasing`（散戶追高）：融資大增，法人同步出貨
> - `distribution`（主力出貨）：法人連賣，成交量放大
> - `neutral`：訊號不明確

---

## 4. 前端展示需求（React + Tailwind）

目標：製作「AI 決策儀表板」，重點是可解釋與可比較。

### 4.1 必要元件

1. **股票代碼輸入框**
   - MVP 入口（例如輸入 2330.TW）

2. **信心指數元件**
   - 圓形進度條顯示 AI confidence（0~100）

3. **雜訊過濾對比視窗**
   - 左：原始新聞
   - 右：純數據摘要（JSON / 條列）

4. **分析路徑圖（流程事件）**
   - 例：「抓取新聞中 → 抽取數值完成 → 驗證歷史資料完成」

### 4.2 UX 要點

- 顯示資料來源與時間戳
- 明確標示「推論」與「事實」區塊
- 長流程分析需有進度狀態（loading / step logs）

---

## 5. 建議技術棧

- **Backend**：Python 3.10+, FastAPI, LangChain, LangGraph
- **數據處理**：
  - `yfinance`：拉取 OHLCV 歷史行情、即時報價
  - `pandas`：計算技術指標（rolling mean、pct_change、自定義 RSI）
  - `ta` / `pandas-ta`（可選）：封裝常用技術分析計算，避免重複造輪
  - `twstock`（可選）：台股三大法人、融資融券資料抓取
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

    return {
        "ma5": round(float(ma5), 2),
        "ma20": round(float(ma20), 2),
        "ma60": round(float(ma60), 2),
        "bias_ma20": round(float(bias_ma20), 2),
        "rsi14": round(float(rsi14), 2),
        "volume_change_pct": round(float(volume_change_pct), 2),
    }
```

---

## 6. MVP 里程碑

### Phase 1（已部分完成）

- yfinance 抓取股票快照
- 新聞清潔 JSON（date/title/numbers/sentiment）

### Phase 2（下一步）

- 導入 LangGraph，建立「資料不足→補抓」回圈
- 加入新聞 RSS 抓取
- 加入計算工具（本益比位階、乖離率）

### Phase 3（前端）

- React 輸入框 + 分析結果頁
- 信心指數與雜訊對比元件
- Agent 分析路徑可視化

---

## 7. 驗收標準（Definition of Done）

- 能輸入股票代碼並觸發完整分析流程
- 當資料不足時，系統會自動補抓後再分析
- **技術面**：必須包含由 Python 函式計算後的 MA5/20/60、BIAS、RSI14、成交量變化數值
- **籌碼面**：必須包含三大法人合計買賣超方向及融資融券變化
- **多維交叉驗證**：當新聞訊號與法人動向背離時，系統須在 `cross_validation_note` 中明確標記警示
- 輸出必須包含：
  - `summary`：去情緒化事實型摘要
  - `sentiment_label`：positive / negative / neutral
  - `technical_signal`：bullish / bearish / sideways（必填）
  - `institutional_flow`：institutional_accumulation / retail_chasing / distribution / neutral（必填）
  - `confidence_score`：0–100，反映三維訊號一致性
  - `cross_validation_note`：說明三維訊號的交叉驗證結論
  - `risks`：風險提示列表
  - `data_sources`：資料來源列表
- 所有數值指標**必須**可追溯至 yfinance / TWSE 原始資料，不得由 LLM 直接生成
- 前端可視化顯示分析過程與最終結論（含三維訊號燈號）

---

## 8. 風險與注意事項

- 資料來源授權與抓取頻率限制（避免封 IP）
- 新聞內容版權與儲存策略
- 模型幻覺風險：需以可驗證資料回填
- 指標定義需版本化，避免不同批次結論不可比
