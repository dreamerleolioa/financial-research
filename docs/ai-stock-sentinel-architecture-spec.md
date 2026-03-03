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

### Prompt / CoT 設計目標

輸入：清潔後新聞 ＋ 技術指標 JSON ＋ 籌碼 JSON（全由 Python 函式計算後傳入）

處理步驟：

1. 提取新聞中所有提及數值（營收、EPS、毛利率、目標價、漲跌幅等）
2. 識別並標記情緒化動詞（例如：崩盤、起飛、噴出），保留事實陳述
3. 讀取技術面工具回傳數值，判斷均線排列、乖離率位階、RSI 超買/超賣
4. 讀取籌碼面工具回傳數值，判斷法人方向與散戶籌碼結構
5. **執行多維交叉驗證**，依上方規則調整信心分數
6. 產出結論、風險、信心值、`Technical_Signal`、`Institutional_Flow`

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
