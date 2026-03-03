# AI Stock Sentinel 實作任務拆解（Execution Plan）

> 版本：v1  
> 更新日期：2026-03-03

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

### P1-2 新聞清潔工（Cleaner Agent）
- **目標**：輸入新聞內容，輸出乾淨 JSON
- **任務**
  - 定義 schema：`date/title/mentioned_numbers/sentiment_label`
  - 實作 LLM 結構化輸出
  - 無 API key 時 fallback heuristic
- **DoD**
  - 支援 `--text` / `--file` / stdin
  - 任何輸入都能穩定輸出 schema

### P1-3 Crawler + Cleaner 整合
- **目標**：同一流程同時輸出股票快照與新聞清潔結果
- **任務**
  - 將 `news_cleaner` 接入 `StockCrawlerAgent`
  - `main.py` 增加 `--news-text` / `--news-file`
- **DoD**
  - 單一指令可得到 `snapshot + analysis + cleaned_news`

---

## Phase 2：LangGraph 協作與補抓回圈

### P2-1 導入 LangGraph 狀態機
- **目標**：從線性流程升級為可回饋流程
- **任務**
  - 定義 graph state（symbol/news/fundamental/data_sufficiency）
  - 建立節點：`crawl -> clean -> analyze -> judge -> (loop)`
- **DoD**
  - 可觸發「資料不足」分支並重新抓取

### P2-2 資料完整性判斷節點
- **目標**：分析前自動判斷資料是否足夠
- **任務**
  - 規則：必要欄位缺失、新聞過舊、數字不足
  - 產出 flags：`requires_news_refresh`, `requires_fundamental_update`
- **DoD**
  - 有明確缺失理由與重跑次數上限

### P2-3 新聞資料源擴充（RSS）
- **目標**：不只靠手動輸入新聞
- **任務**
  - Google News RSS 抓取器
  - 標準化來源 metadata
- **DoD**
  - 指定 symbol 可拉回至少 N 篇新聞

---

## Phase 3：分析能力強化

### P3-1 去情緒化分析流程
- **目標**：輸出「事實」與「情緒詞標記」
- **任務**
  - 建立情緒詞字典（中英）
  - 抽取數字 + 情緒詞標記 + 事實敘述
- **DoD**
  - 回傳結構包含 `facts`, `emotional_terms`, `fact_only_summary`

### P3-2 Tool Use 計算工具
- **目標**：Agent 可呼叫計算工具驗證指標
- **任務**
  - 計算工具：本益比位階、乖離率、YoY/MoM
  - 接入 Analysis Agent 的工具呼叫
- **DoD**
  - 至少 2 個指標可自動計算並附公式/輸入來源

### P3-3 信心分數
- **目標**：讓輸出有可解釋置信度
- **任務**
  - 設計 confidence 規則（資料完整度、來源品質、指標一致性）
- **DoD**
  - 輸出 `confidence_score`（0~100）與計分依據

---

## Phase 4：前端儀表板（React + Tailwind）

### P4-1 前端基礎專案
- **目標**：建立可執行前端骨架
- **任務**
  - 建立 React + TypeScript + Tailwind
  - 股票輸入框 + 查詢按鈕
- **DoD**
  - 可輸入 symbol 並呼叫後端 API

### P4-2 核心展示元件
- **目標**：呈現 AI 分析價值
- **任務**
  - 信心指數圓形元件
  - 雜訊過濾左右對照
  - 分析路徑（step timeline）
- **DoD**
  - 三個元件可顯示真實後端資料

### P4-3 體驗優化
- **目標**：提升可讀性與可追溯性
- **任務**
  - 顯示來源、時間戳
  - 進度狀態（loading/step logs）
- **DoD**
  - 使用者可看懂結論來源與過程

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

---

## 建議執行順序（短版）

1. 完成 P2-1 + P2-2（先有 LangGraph 回圈）
2. 完成 P2-3（新聞來源自動化）
3. 完成 P3-1 + P3-2 + P3-3（分析可用且可解釋）
4. 完成 P4-1~P4-3（前端展示）
