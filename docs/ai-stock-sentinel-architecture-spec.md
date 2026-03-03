# AI Stock Sentinel 技術架構需求文件

> 日期：2026-03-03  
> 狀態：Draft v1  
> 目的：將產品需求大綱轉為可落地的工程實作藍圖

## 1. 目標與方向

AI Stock Sentinel 採用 TypeScript + Python 混合架構，核心目標為：

- 建立可循環補資料的多 Agent 流程
- 將財經資訊去噪後再分析，降低情緒性語句干擾
- 將分析過程透明化，前端可視化呈現 AI 決策路徑

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

負責蒐集分析所需資料，並保留來源與時間戳，供後續可追溯。

### 資料源建議

- **基本面**
  - 財報狗 / 公開資訊觀測站（優先採 API 或穩定封裝庫）
  - 目標：避免高頻爬 HTML 被封鎖

- **即時新聞**
  - Google News RSS
  - 指定財經媒體 RSS / API

### 抓取工具建議

- 優先：**Firecrawl**、**Browserbase**
- 原因：可直接輸出乾淨 Markdown，降低自行清理 HTML 成本

### 輸出（範例）

```json
{
  "symbol": "2330.TW",
  "fetched_at": "2026-03-03T08:00:00Z",
  "fundamentals": {...},
  "news": [
    {
      "source": "google-news-rss",
      "url": "...",
      "title": "...",
      "content_markdown": "..."
    }
  ]
}
```

## 3.2 Analysis Agent（參謀官）

### 角色

核心分析引擎，主責「去情緒化 + 事實驗證 + 指標計算」。

### Prompt / CoT 設計目標

輸入：原始新聞全文（或清潔後內容）

處理步驟：

1. 提取所有提及數值（營收、EPS、毛利率、目標價、漲跌幅等）
2. 識別並標記情緒化動詞（例如：崩盤、起飛、噴出）
3. 過濾只保留事實陳述
4. 與歷史數據對照驗證（例如 YoY/MoM、估值區間）
5. 產出結論、風險、信心值

> 註：若做正式上線，建議將「內部推理」與「對外輸出」分離，避免過度暴露模型中間推理內容。

### Tool Use（建議最小集合）

- 計算工具（calculator）
  - 本益比位階
  - 乖離率
  - 簡易成長率換算

- 時序資料查詢工具
  - 近期營收、法人買賣超、價格區間

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

- **Backend**：Python 3.10+, FastAPI, LangChain, LangGraph, yfinance
- **Crawler**：Firecrawl / Browserbase（擇一先導入）
- **Frontend**：React, Tailwind CSS, TypeScript
- **Storage（可選）**：PostgreSQL / SQLite（MVP 可先檔案化）
- **Queue（可選）**：Celery / RQ（若後續要批次分析）

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
- 輸出包含：
  - 事實型摘要
  - 情緒詞標記結果
  - 至少 2 個可驗證數值指標
  - 信心分數與資料來源
- 前端可視化顯示分析過程與最終結論

---

## 8. 風險與注意事項

- 資料來源授權與抓取頻率限制（避免封 IP）
- 新聞內容版權與儲存策略
- 模型幻覺風險：需以可驗證資料回填
- 指標定義需版本化，避免不同批次結論不可比
