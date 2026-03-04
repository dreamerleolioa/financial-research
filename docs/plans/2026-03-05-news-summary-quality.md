# 2026-03-05 計劃：新聞摘要品質優化（News Summary Quality Gate）

## 背景

目前 `cleaned_news` 在部分案例會出現以下問題：
- `title` 退化為時間戳字串（例如 RSS 發佈時間）
- `date` 為 `unknown` 但缺乏品質警示
- `mentioned_numbers` 混入日期碎片，降低可讀性

此問題已提升為需求層級修正，目標是讓摘要在「可讀性、可信度、可追溯性」達到可驗收標準。

## 目標

1. 新聞摘要欄位可讀且有語意，不再以時間戳取代事件標題
2. 摘要品質可量化（score + flags），前端可明確提示風險
3. 關鍵數字更聚焦財經語意，減少雜訊

## 範圍

### In Scope
- `cleaned_news` 品質檢查規則
- 新增 `cleaned_news_quality`（`quality_score`、`quality_flags`）
- API 回傳結構擴充
- 前端低品質提示狀態
- 對應測試（單元 + 整合）

### Out of Scope
- 多語新聞翻譯品質優化
- 新聞事件分群與主題建模
- 進階摘要改寫模型更換

## 具體任務

### Task NQ-1：標題品質檢查
- 新增規則：若 `title` 為純時間戳、純 URL、純來源代碼，標記 `TITLE_LOW_QUALITY`
- 若命中規則，回退至可用事件標題來源（如 RSS title 清洗後文本）

### Task NQ-2：日期正規化與旗標
- 優先解析為 ISO 8601；可接受 RFC 2822 輸入
- 解析失敗保留 `unknown`，同時標記 `DATE_UNKNOWN`

### Task NQ-3：關鍵數字去噪
- 過濾純日期碎片（年/月/日切片）
- 保留財經語意數值（%、金額、EPS、目標價、量價）
- 命中「無有效財經數字」時標記 `NO_FINANCIAL_NUMBERS`

### Task NQ-4：品質分數機制
- 以 rule-based 產生 `quality_score`（0-100）
- 參考扣分：
  - `TITLE_LOW_QUALITY`: -35
  - `DATE_UNKNOWN`: -15
  - `NO_FINANCIAL_NUMBERS`: -20
- 最終分數 clamp 至 `[0, 100]`

### Task NQ-5：前端提示
- 若 `quality_score < 60` 或 flags 非空，顯示「摘要品質受限」提示
- 保持主流程不中斷，不影響分析卡片渲染

### Task NQ-6：測試補齊
- 單元測試：品質規則覆蓋（標題、日期、數字過濾、分數計算）
- API 測試：`cleaned_news_quality` 欄位存在與格式正確
- 前端測試（若有測試基礎）：低品質提示可正確顯示

## 驗收標準（DoD）

- 不再出現「時間戳直接當標題」的摘要輸出
- `date=unknown` 必伴隨 `DATE_UNKNOWN` 旗標
- `mentioned_numbers` 的日期碎片案例可被測試攔截
- API 穩定回傳 `cleaned_news_quality`
- 前端在低品質摘要顯示明確提示文案

## 風險與注意事項

- 不同 RSS 來源格式差異大，需保留寬鬆解析與 fallback
- 規則過嚴可能誤刪有效資訊，需以回歸測試保護
- 品質分數只做「摘要可信度」訊號，不等同交易建議品質

## 預估順序

1. 後端 quality rules（NQ-1~NQ-4）
2. API response 擴充
3. 前端提示整合（NQ-5）
4. 測試補齊（NQ-6）
