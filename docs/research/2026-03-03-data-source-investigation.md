# 台股籌碼資料來源調研紀錄（2026-03-03）

## 1) 研究背景
為了支援 `AI Stock Sentinel` 的多維度分析（特別是「法人/籌碼歸屬」與「Quant → Qual」），需要建立穩定、可替換的資料來源策略，避免單一供應商失效造成流程中斷。

## 2) 研究目標
- 找到可支援以下欄位的資料來源：
  - `foreign_buy`
  - `investment_trust_buy`
  - `dealer_buy`
  - `margin_delta`（融資融券變化）
- 評估可行性：可用性、限流/配額、穩定度、整合成本、備援可行性。
- 產出 v1 可落地的 `Primary + Fallback` 策略。

## 3) 調研範圍
- FinMind（官方文件/套件頁）
- TWSE OpenAPI（Swagger/OAS）
- TPEX（官網與資訊中心）
- twstock（文件與套件）
- fugle-marketdata（文件與套件）
- finlab（套件與站點）

## 4) 來源與證據摘要

### 4.1 FinMind
- 文件與描述顯示包含台股籌碼面資料（含三大法人、融資融券）。
- 具 API 請求上限與 token 方案，可透過配額升級降低阻塞。
- 結論：最符合籌碼資料主來源需求。

### 4.2 TWSE OpenAPI
- 已驗證官方 OpenAPI 規格與入口：
  - Base URL：`https://openapi.twse.com.tw/v1`
  - Swagger：`https://openapi.twse.com.tw/v1/swagger.json`
- 可觀察到與本案相關端點族群：
  - `exchangeReport/MI_MARG`（融資融券餘額）
  - `fund/MI_QFIIS_*`（外資/陸資相關）
- 結論：官方、穩定、可作為強力備援（尤其上市資料）。

### 4.3 TPEX
- 官網與 InfoHub 明確提供「三大法人」「信用交易」等資訊入口。
- 本輪抓取到的多為入口頁與導覽，未取得同等乾淨、可直接對接的完整 API 規格。
- 結論：可用，但在「程式化穩定取數」層面需要額外梳理；先作第二備援較務實。

### 4.4 twstock
- 有明確來源（TWSE/TPEX）與請求頻率限制提醒（如 TWSE 端限制）。
- 套件定位較偏行情與常用查詢工具，對本案「籌碼欄位完整覆蓋」不是最佳主來源。
- 結論：可留作低優先備援（例如價格/行情補洞），不建議當籌碼主供應商。

### 4.5 fugle-marketdata
- 主要強項是即時報價/成交資料（REST + WebSocket）。
- 與本案重點（法人買賣超、融資融券）不完全對位。
- 結論：不列入本次籌碼主流程。

### 4.6 finlab
- 套件能力強，資料與回測整合成熟。
- 但本案現階段需要的是「可控、可替換的資料供應層（provider abstraction）」；授權與使用邊界需另行檢核。
- 結論：可作參考工具，不列入 v1 即時資料主供應鏈。

## 5) 比較矩陣（v1 實務導向）
| 方案 | 法人/籌碼覆蓋 | 融資融券 | 穩定/官方性 | 整合成本 | v1 角色 |
|---|---|---|---|---|---|
| FinMind | 高 | 高 | 中（商用 API） | 低~中 | Primary |
| TWSE OpenAPI | 中~高（上市面向強） | 高（MI_MARG） | 高（官方） | 中 | Fallback #1 |
| TPEX | 中（資訊有） | 中（資訊有） | 高（官方站） | 中~高（需整理取數） | Fallback #2 |
| twstock | 中 | 中 | 中 | 低 | 低優先備援 |
| fugle-marketdata | 低（非主打） | 低（非主打） | 中 | 中 | 不採用 |
| finlab | 視授權/資料庫而定 | 視資料庫而定 | 中 | 中 | 參考工具 |

## 6) 決策（建議定案）
- `Primary`: `FinMindProvider`
- `Fallback #1`: `TwseOpenApiProvider`（先覆蓋上市）
- `Fallback #2`: `TpexProvider`（上櫃，逐步補齊）
- `twstock`: 僅保留在低優先備援，不進入核心籌碼主流程

## 7) 風險與對策

### 7.1 限流/配額風險
- 風險：API hit rate 過高造成失敗或延遲。
- 對策：
  - Provider 層統一節流（rate limit）
  - 快取（短期 in-memory + 可選持久化）
  - 指數退避重試（exponential backoff）

### 7.2 欄位異動/格式漂移
- 風險：供應商欄位名稱、型別變更導致解析失敗。
- 對策：
  - 在 provider 內實作 schema mapping
  - 建立欄位完整性檢查（required fields）
  - 啟動健康檢查腳本每日驗證

### 7.3 上市/上櫃資料碎片化
- 風險：上市與上櫃資料分散，跨來源合併複雜。
- 對策：
  - 統一 internal domain model（symbol/date/flow/margin schema）
  - 合併規則在 router 層集中管理，避免散落各節點

## 8) 實作前置清單（對應 Session 1）
1. 建立 `InstitutionalFlowProvider` 介面
2. 實作 `FinMindProvider`（主）
3. 實作 `TwseOpenApiProvider`（備援一）
4. 建立 provider router（primary -> fallback）
5. 撰寫驗證腳本：`2330.TW` 最近 N 日必得欄位檢查
6. 產生失敗原因分類與告警訊息（方便日後監控）

## 9) 驗收標準（DoD）
- `2330.TW` 能穩定取得：`foreign_buy`, `investment_trust_buy`, `dealer_buy`, `margin_delta`
- 單一來源失敗時可自動切換至 fallback
- 欄位缺漏時回傳可診斷錯誤，不 silent fail
- 具基本速率控制與重試策略

## 10) 備註
- 本文件為「研究過程與選型決策」紀錄，不取代最終系統規格。
- 若後續商業授權/成本條件變動，需重跑一次供應商評估。