# AI Stock Sentinel 持股診斷系統技術規格（v1.0）

> 類型：獨立功能擴展文件
> 日期：2026-03-09
> 狀態：Draft v1.0
> 定位：針對已持有倉位提供動態風險評估與出場決策支援
> API 端點：`POST /analyze/position`

---

## 1. 系統定位：從「偵察」轉向「守護」

本系統與 `POST /analyze`（個股偵察）在邏輯層級上**完全分離**：

| 維度         | 個股偵察（`/analyze`） | 持股診斷（`/analyze/position`）     |
| ------------ | ---------------------- | ----------------------------------- |
| 核心錨點     | 當前市場價格           | 使用者**購入成本價（Entry Price）** |
| 主要目的     | 尋找買點（進攻）       | 管理倉位風險（防守）                |
| 策略輸出     | 建議入場區間、停損位   | 建議出場/減碼區間、移動停利位       |
| LLM 推理模式 | 標準偵察模式           | 持股初衷檢視（Skeptic Mode 升級版） |

本系統不重新尋找買點，而是將 `entry_price` 作為核心錨點，對撞當前技術、籌碼、消息與基本面數據，產出戰術建議。

---

## 2. 輸入規格（Request Schema）

### `POST /analyze/position`

**Request Body**

```json
{
  "symbol": "2330.TW",
  "entry_price": 980.0,
  "entry_date": "2026-01-15",
  "quantity": 1000
}
```

**欄位說明**

| 欄位          | 型別              | 必填 | 說明                               |
| ------------- | ----------------- | ---- | ---------------------------------- |
| `symbol`      | string            | ✅   | 股票代碼（例：`2330.TW`）          |
| `entry_price` | float             | ✅   | 購入成本價，作為診斷核心錨點       |
| `entry_date`  | string (ISO 8601) | ❌   | 購入日期，用於對比購入後的訊號變化 |
| `quantity`    | int               | ❌   | 持有數量，用於計算損益金額         |

---

## 3. 後端邏輯：持股診斷專屬設計

### 3.1 倉位位階計算（`preprocess_node` 擴充）

在 Python 層計算以下數據後餵入 LLM，**禁止 LLM 自行計算數值**：

| 計算項目                 | 公式                                                | 說明                 |
| ------------------------ | --------------------------------------------------- | -------------------- |
| `profit_loss_pct`        | `(current_price - entry_price) / entry_price × 100` | 當前損益百分比       |
| `cost_buffer_to_support` | `entry_price - support_20d`                         | 成本價與支撐位的距離 |
| `position_status`        | rule-based 判斷（見下表）                           | 倉位健康度標籤       |

**`position_status` 判斷規則**：

| 條件                                                  | 標籤              | 中文說明       |
| ----------------------------------------------------- | ----------------- | -------------- |
| `profit_loss_pct > 5%` 且 `entry_price > support_20d` | `profitable_safe` | 獲利脫離成本區 |
| `-5% <= profit_loss_pct <= 5%`                        | `at_risk`         | 成本邊緣震盪   |
| `profit_loss_pct < -5%`                               | `under_water`     | 套牢防守期     |

**`position_status_narrative` 敘事（供 LLM 讀取）**：

- `profitable_safe`：「目前獲利已脫離成本區，持股安全緩衝充足。」
- `at_risk`：「股價正在成本價附近震盪，需密切觀察支撐是否有效。」
- `under_water`：「目前處於套牢狀態，需評估停損或攤平策略。」

### 3.2 移動停利／停損模型（`strategy_node` 升級）

根據損益狀態調整 `stop_loss` 邏輯（**rule-based Python，不由 LLM 計算**）：

**保本機制**：

- 條件：`profit_loss_pct >= 5%`
- 規則：`trailing_stop = max(entry_price, support_20d)`（停損位至少上移至成本價）

**移動停利（Trailing Stop）**：

- 條件：當前收盤價 >= 近 20 日最高點（`close >= high_20d`）
- 規則：`trailing_stop = max(MA10, support_20d)`（防守位隨之調整）

**套牢防守**：

- 條件：`profit_loss_pct < -5%`
- 規則：`trailing_stop = entry_price × 0.93`（預設持守成本價 -7% 以內）

### 3.3 訊號衝突仲裁：持股初衷檢視（Skeptic Mode 升級）

LLM 在 `analyze_node` 中必須執行「持股初衷檢視」邏輯：

1. 讀取購入時的市場背景（若提供 `entry_date`，對比購入後法人方向變化）
2. 若法人資金流向轉為 `distribution`（出貨），**即使獲利中**，也須發出「獲利了結」警示
3. 若 `position_status = under_water` 且 `flow_label = distribution`，升級為「立即停損評估」警示

**持股初衷檢視 System Prompt 補充段落**：

```
你正在診斷使用者的持有倉位，而非尋找新的買點。

核心任務：
1. 以購入成本價（entry_price）為錨點，判斷當前持股是否值得繼續持有
2. 法人資金方向是最重要的出場訊號——若法人轉為出貨，獲利中亦須警示了結
3. 你的輸出必須聚焦「出場」、「減碼」、「防守」，不得建議加碼或新進場
4. 禁止以樂觀語氣淡化風險；若存在出場理由，必須明確標記

出場觸發條件（任一命中即須評估出場）：
- flow_label = distribution 且 profit_loss_pct > 0（獲利出場警示）
- flow_label = distribution 且 position_status = under_water（停損評估）
- technical_signal = bearish 且 close < trailing_stop（跌破防守線）
```

---

## 4. 輸出規格（Response Schema）

在原有 `/analyze` 輸出基礎上，**新增** `position_analysis` 物件：

```json
{
  "symbol": "2330.TW",
  "current_price": 1105.0,
  "position_analysis": {
    "entry_price": 980.0,
    "profit_loss_pct": 12.5,
    "position_status": "profitable_safe",
    "position_narrative": "目前獲利已脫離成本區，持股安全緩衝充足。",
    "recommended_action": "Hold",
    "trailing_stop": 1025.0,
    "trailing_stop_reason": "獲利超過 5%，停損位上移至成本價保本",
    "exit_reason": null
  },
  "confidence_score": 72,
  "technical_signal": "bullish",
  "institutional_flow": "institutional_accumulation",
  "tech_insight": "...",
  "inst_insight": "...",
  "news_insight": "...",
  "final_verdict": "..."
}
```

**`position_analysis` 欄位說明**：

| 欄位                   | 型別           | 說明                                    | 範例                                          |
| ---------------------- | -------------- | --------------------------------------- | --------------------------------------------- |
| `entry_price`          | float          | 購入成本價（回傳確認）                  | `980.0`                                       |
| `profit_loss_pct`      | float          | 當前損益百分比                          | `12.5`                                        |
| `position_status`      | string         | 倉位健康度標籤                          | `profitable_safe` / `at_risk` / `under_water` |
| `position_narrative`   | string         | 倉位狀態敘事（rule-based，供 LLM 讀取） | `"目前獲利已脫離成本區..."`                   |
| `recommended_action`   | string         | 持股戰術指令                            | `Hold` / `Trim` / `Exit`                      |
| `trailing_stop`        | float          | 動態調整後的防守價位                    | `1025.0`                                      |
| `trailing_stop_reason` | string         | 停利/停損邏輯說明                       | `"獲利超過 5%，停損位上移至成本價保本"`       |
| `exit_reason`          | string \| null | 觸發減碼/出場的核心理由（無則 null）    | `"法人連續大賣且跌破 MA5，建議保護獲利"`      |

**`recommended_action` 判斷規則（rule-based Python）**：

| 條件                                                        | 輸出                 |
| ----------------------------------------------------------- | -------------------- |
| `flow_label = distribution` 且 `profit_loss_pct > 0`        | `Trim`（建議減碼）   |
| `flow_label = distribution` 且 `profit_loss_pct <= 0`       | `Exit`（建議出場）   |
| `technical_signal = bearish` 且 `close < trailing_stop`     | `Exit`（跌破防守線） |
| `position_status = under_water` 且 `profit_loss_pct < -10%` | `Exit`（深度套牢）   |
| 其他                                                        | `Hold`（續抱）       |

---

## 5. GraphState 擴充

在現有 `GraphState` 基礎上新增以下欄位，**僅於 `/analyze/position` 流程啟用**：

```python
class PositionState(TypedDict, total=False):
    # 輸入欄位
    entry_price: float
    entry_date: str | None
    quantity: int | None

    # preprocess_node 計算結果
    profit_loss_pct: float
    cost_buffer_to_support: float
    position_status: str          # profitable_safe | at_risk | under_water
    position_narrative: str

    # strategy_node 計算結果
    trailing_stop: float
    trailing_stop_reason: str
    recommended_action: str       # Hold | Trim | Exit
    exit_reason: str | None
```

---

## 6. 前端介面需求（持股診斷介面）

### 6.1 頁面入口

入口位於「我的持股」列表頁中，由使用者針對單一持股觸發即時診斷；不再維持獨立的 `PositionPage` 路由。

觸發診斷時，前端從持股資料直接帶入：

- 股票代碼
- 購入成本價
- 購入日期（若有）
- 持有數量（若有）

### 6.2 持股診斷儀表板元件

**損益對照卡**：

- 視覺化進度條：顯示成本價 → 現價 → 壓力位的位置關係
- 標註關鍵價位：`entry_price`、`trailing_stop`、`support_20d`、`resistance_20d`
- 顏色語意：獲利（綠）/ 成本邊緣（黃）/ 套牢（紅）

**倉位狀態標籤**：

- `profitable_safe` → 🟢 「獲利安全區」
- `at_risk` → 🟡 「成本邊緣」
- `under_water` → 🔴 「套牢防守」

**戰術建議卡（Action Plan，持股版）**：

- 操作方向：`Hold`（續抱）/ `Trim`（減碼）/ `Exit`（出場）（取代個股版的「分批佈局」）
- 防守底線：`trailing_stop`（動態更新，非靜態停損）
- 出場理由：`exit_reason`（有值時以紅色警示框顯示）

**三維 Insight 卡片（持股版調整）**：

- 技術面：強調「目前的防守線在哪裡」（非「在哪裡買」）
- 籌碼面：強調「主力是否在撤退」
- 消息面：強調「是否有改變趨勢的利空」

### 6.3 差異對照（與個股分析版比較）

| UI 元件        | 個股分析版                 | 持股診斷版                         |
| -------------- | -------------------------- | ---------------------------------- |
| 信心指數卡     | 同                         | 同（不變）                         |
| 戰術卡操作方向 | 觀望 / 分批佈局 / 持股續抱 | **續抱 / 減碼 / 出場**             |
| 戰術卡入場區間 | `entry_zone`               | ❌ 不顯示                          |
| 戰術卡防守底線 | `stop_loss`（靜態）        | **`trailing_stop`（動態）**        |
| 新增元件       | —                          | **損益對照卡**、**出場理由警示框** |

---

## 7. 驗收標準（Definition of Done）

- 輸入 `symbol` + `entry_price` 可觸發持股診斷流程，與 `/analyze` 完全獨立
- `profit_loss_pct`、`position_status`、`trailing_stop` 必須由 Python rule-based 計算，不由 LLM 估算
- `recommended_action` 必須依 4 節規則產出，LLM 不得覆寫
- 當 `flow_label = distribution` 且獲利中，`exit_reason` 不得為 null
- 前端損益對照卡正確標註成本價、防守位、支撐壓力位
- 當 `recommended_action = Exit` 時，前端以紅色警示框顯示 `exit_reason`
- 所有技術指標數值與籌碼數據沿用現有 `calculate_technical_indicators` 與 `fetch_institutional_flow` 工具

---

## 8. 任務拆解（Phase 6 Roadmap）

| Task   | 說明                                                                             | 主要異動檔案                                                       |
| ------ | -------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Task 1 | 建立 `POST /analyze/position` 路由與 `PositionState` GraphState 欄位             | `backend/src/api.py`、`backend/src/graph_state.py`                 |
| Task 2 | 實作 `PositionScorer`（損益位階計算、移動停利邏輯、`recommended_action` 規則）   | `backend/src/analysis/position_scorer.py`                          |
| Task 3 | 撰寫持股診斷專用 System Prompt 段落（持股初衷檢視、出場推理強化）                | `backend/src/langchain_analyzer.py`                                |
| Task 4 | 前端開發「我的持股」頁內的持股診斷介面（損益對照卡、持股版戰術卡、出場警示元件） | `frontend/src/pages/PortfolioPage.tsx`、`frontend/src/components/` |

---

## 9. 風險與注意事項

- `entry_price` 為使用者自填數值，需做基本合理性驗證（正數、不超過歷史最高價一定倍數）
- `trailing_stop` 會隨每次查詢動態計算，前端需標示「本次診斷時間點」，避免使用者誤以為是靜態設定
- 本系統**不儲存**使用者倉位資訊（MVP 階段），每次查詢需重新輸入 `entry_price`
- 出場建議屬於參考性質，需在 UI 明確標示免責聲明
