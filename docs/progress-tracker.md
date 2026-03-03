# AI Stock Sentinel 進度追蹤

> 更新日期：2026-03-03

## 目前完成度（高層）

- **Phase 1（MVP Backend）**：約 85%（核心流程可跑）
- **Phase 2（LangGraph 回圈）**：約 30%（骨架可跑，judge 為 stub）
- **Phase 3（分析能力強化）**：約 20%（有基礎清潔與情緒標籤）
- **Phase 4（前端儀表板）**：約 35%（前端骨架與核心視覺元件已完成）

---

## 已完成 ✅

### 專案與環境
- [x] Git 儲存庫初始化
- [x] `backend/venv` 建立與依賴安裝
- [x] `Makefile`（`make install`, `make run`）
- [x] `.gitignore` 建立

### Crawler / Cleaner 核心
- [x] `yfinance` 抓取股票快照（預設 `2330.TW`）
- [x] Stock 分析介面（有 LLM 時分析，無金鑰 fallback）
- [x] 財經新聞清潔器 schema（`date/title/mentioned_numbers/sentiment_label`）
- [x] 新聞清潔器支援 `--text` / `--file` / stdin
- [x] 新聞清潔已整合到 `StockCrawlerAgent`（輸出 `cleaned_news`）

### 文件
- [x] README（安裝、執行、參數、輸出）
- [x] 技術架構需求文件
- [x] 任務拆解文件（本次新增）
- [x] 開發手冊新增「完成即補測試」規範
- [x] 後端 API 技術規格文件（`docs/backend-api-technical-spec.md`）

### API / 測試（本次新增）
- [x] 新增 FastAPI `/health`、`/analyze`
- [x] 新增 API 合約測試（健康檢查、成功路徑、驗證錯誤）

### 前端儀表板（基礎）
- [x] React + TypeScript + Tailwind 專案初始化
- [x] 股票代碼輸入框（MVP）
- [x] 信心指數元件（靜態）
- [x] 雜訊過濾左右對照（靜態）
- [x] 分析路徑圖（靜態）

---

## 進行中 / 待完成 ⏳

### Phase 2：LangGraph
- [x] 建立 LangGraph 狀態機（GraphState + 節點 stub + builder）
- [x] loop guard（max_retries）骨架
- [ ] 完整性判斷節點（judge 邏輯填入）
- [ ] 新聞 RSS 自動抓取

### Phase 3：分析深化
- [ ] 事實萃取 + 情緒詞標記分離輸出
- [ ] Tool Use（本益比位階、乖離率、YoY/MoM）
- [ ] 信心分數與依據

### Phase 4：前端
- [ ] 串接後端 API（symbol -> 真實分析結果）
- [ ] 元件改為真實資料驅動（非靜態假資料）
- [ ] 補上錯誤狀態與載入狀態

---

## 目前可用指令

```bash
cd backend
make run
```

```bash
cd backend
PYTHONPATH=src ./venv/bin/python -m ai_stock_sentinel.main --symbol 2330.TW --news-text "2026-03-03 ..."
```

```bash
cd backend
./venv/bin/python agent.py --text "2026-03-03 ..."
```

---

## 下一步建議（Top 3）

1. 完成 AnalyzeResponse v1 契約鎖定（補 `errors` 與欄位文件）
2. 導入 LangGraph，完成資料不足補抓回圈（Phase 2 核心）
3. 補上 RSS 新聞抓取，降低手動貼新聞成本
