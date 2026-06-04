Phase 1 Prompt
請只實作 Single Trade Review 的 Phase 1：persistence + API skeleton。

參考文件：
docs/plans/2026-06-04-single-trade-review-analysis.md

本階段目標：

1. 新增 trade_review DB model 與 Alembic migration。
2. 新增 GET /portfolio/{portfolio_id}/review。
3. 新增 POST /portfolio/{portfolio_id}/review。
4. POST 第一次呼叫時建立一筆 minimal saved review。
5. POST 第二次呼叫時直接回傳已保存 review，不重新產生。
6. 僅支援已結案 portfolio；active portfolio 必須拒絕。
7. 僅允許目前登入使用者操作自己的 portfolio。
8. review_result 與 evidence_payload 必須同一 transaction 寫入，失敗不可留下半成品。

嚴格限制：

- 不做任何技術指標計算。
- 不做 market_regime。
- 不做 entry/exit classification。
- 不做前端。
- 不接 LLM。
- 不改 /analyze 或 /analyze/position。
- 不實作 refresh。
- 不做完整 evidence payload，只放 minimal placeholder 結構，但 key 要與計劃一致。

最小 review_result 可包含：

- data_quality
- trade_result
- entry_review
- holding_review
- exit_review
- operation_review

最小 evidence_payload 可包含：

- trade
- path_metrics
- entry_indicators
- exit_indicators
- detected_events
- data_quality

測試要求：

- 新增或更新後端測試，覆蓋：
  1. closed portfolio 第一次 POST 會建立 review
  2. 第二次 POST 回傳既有 review，不重建
  3. GET 回傳既有 review
  4. active portfolio 不允許 review
  5. 非本人 portfolio 不允許存取
  6. review_result 與 evidence_payload 缺一不可
- 跑相關 pytest。
- 完成後停止，不要進入 Phase 2。

---

Phase 2 Prompt
請只實作 Single Trade Review 的 Phase 2：最小技術資料與 evidence payload。

參考文件：
docs/plans/2026-06-04-single-trade-review-analysis.md

前提：
Phase 1 已完成 trade_review persistence 與 GET/POST API skeleton。

本階段目標：

1. 新增 backend/src/ai_stock_sentinel/analysis/trade_review.py。
2. 在 POST /portfolio/{portfolio_id}/review 首次建立 review 時，產生真實 trade_result 與 evidence_payload。
3. 計算最小交易路徑指標：
   - realized_return_pct
   - holding_days
   - max_profit_pct
   - max_drawdown_pct
   - profit_giveback_pct
   - highest_close_during_holding
   - lowest_close_during_holding
4. 產出最小 entry_indicators：
   - ma20
   - ma60
   - rsi14
   - entry_vs_ma20_pct
   - entry_vs_ma60_pct
   - volume_ratio 若資料足夠
5. 產出最小 exit_indicators：
   - ma20
   - ma60
   - rsi14
   - exit_vs_ma20_pct
   - exit_vs_ma60_pct
   - volume_ratio 若資料足夠
6. evidence_payload 不可存完整 OHLCV / K-line arrays。
7. 若資料不足，回傳 data_quality notes 與 insufficient_data，不要硬判斷。

嚴格限制：

- 不做 entry classification。
- 不做 exit classification。
- 不做 market_regime。
- 不做 confidence/caveats。
- 不做前端。
- 不接 LLM。
- 不實作 refresh。
- 不修改 Phase 1 的 API 語意：已存在 review 就直接回傳，不重算。

測試要求：

- trade_review.py 單元測試：
  1. max_profit_pct / max_drawdown_pct / profit_giveback_pct 正確
  2. entry/exit indicator 使用 point-in-time 切片，不偷用未來資料
  3. evidence_payload 不包含完整 OHLCV arrays
  4. 資料不足時產生 data_quality notes
- API 測試：
  1. 首次 POST 會保存真實 evidence_payload
  2. 第二次 POST 不重算
- 跑相關 pytest。
- 完成後停止，不要進入 Phase 3。

---

Phase 3 Prompt
請只實作 Single Trade Review 的 Phase 3：分類規則、market regime、confidence/caveats。

參考文件：
docs/plans/2026-06-04-single-trade-review-analysis.md

前提：
Phase 1 persistence/API 已完成。
Phase 2 最小 trade metrics 與 evidence_payload 已完成。

本階段目標：

1. 在 trade_review.py 補 market_regime 偵測：
   - uptrend
   - downtrend
   - range_bound
   - strong_momentum
   - high_volatility
   - insufficient_data
2. 補 entry_review.classification：
   - breakout_entry
   - pullback_entry
   - chase_entry
   - weak_entry
   - range_entry
   - insufficient_data
3. 補 exit_review.classification：
   - profit_protection_exit
   - stop_loss_exit
   - late_stop_exit
   - early_profit_exit
   - panic_exit
   - technical_break_exit
   - insufficient_data
4. 補 holding_review.detected_events，最多保留重要事件，不存完整 K 線。
5. 每個 classification 要包含：
   - classification
   - confidence: high | medium | low
   - market_regime
   - supporting_signals
   - conflicting_signals
   - caveats
6. 維持 point-in-time：
   - entry 只能用 entry_date 以前資料
   - exit 只能用 exit_date 以前資料
   - full-window 指標只能放 trade_result，不可拿來批評 entry/exit

嚴格限制：

- 不做前端。
- 不接 LLM。
- 不實作 refresh。
- 不做全體交易統計。
- 不改 /analyze 或 /analyze/position。
- 不追求完美門檻，先用簡單、可測、保守的規則。
- 如果某個 classification 規則不確定，給 medium/low confidence + caveat，不要擴大研究。

測試要求：

- market_regime 單元測試
- entry classification 單元測試
- exit classification 單元測試
- confidence/caveats 單元測試
- point-in-time 測試：entry/exit 不可使用未來資料
- storage boundary 測試：不存完整 OHLCV arrays
- 跑相關 pytest。
- 完成後停止，不要進入 Phase 4。

---

Phase 4 Prompt
請只實作 Single Trade Review 的 Phase 4：ClosedPortfolioPage 前端整合。

參考文件：
docs/plans/2026-06-04-single-trade-review-analysis.md

前提：
Phase 1-3 後端 API 已完成。

本階段目標：

1. 在 frontend/src/pages/ClosedPortfolioPage.tsx 的每筆 closed trade row 加上「檢討分析」按鈕。
2. 點擊後：
   - 先嘗試 GET /portfolio/{id}/review
   - 若不存在，再呼叫 POST /portfolio/{id}/review 產生
   - 若已存在，直接顯示保存結果
3. 新增 review modal 或 expanded panel。
4. 顯示區塊：
   - 交易結果
   - 進場檢討
   - 持有路徑
   - 出場檢討
   - 下次規則
   - 資料品質提示
5. 新增「複製指標資料」按鈕，複製 pretty-printed evidence_payload JSON。
6. 保留現有期間篩選與已實現損益統計，不破壞既有功能。

嚴格限制：

- 不改後端。
- 不新增全體交易統計圖表。
- 不做 LLM 摘要 UI。
- 不做 refresh / 重新分析按鈕。
- 不重構整個 ClosedPortfolioPage，只做必要增量。
- 不改 AnalyzePage 或 PortfolioPage。

驗證要求：

- 跑 frontend build。
- 若有 lint/build 問題，只修本次造成的問題。
- 手動檢查：
  1. closed row 有檢討分析按鈕
  2. review modal 可以打開
  3. loading/error 顯示正常
  4. 複製指標資料可用
  5. 原本期間篩選與 realized PnL summary 還正常
- 完成後停止。

---

Phase 5 Prompt
請只實作 Single Trade Review 的 Phase 5：收斂與文件同步，不新增功能。

參考文件：
docs/plans/2026-06-04-single-trade-review-analysis.md

前提：
Phase 1-4 已完成。

本階段目標：

1. 跑後端相關測試。
2. 跑前端 build。
3. 檢查 API response 與文件中的 response shape 是否一致。
4. 檢查 evidence_payload 是否沒有完整 OHLCV arrays。
5. 檢查 saved review 是否不會重複生成。
6. 若有文件落差，更新文件。
7. 整理最終實作摘要。

嚴格限制：

- 不新增新功能。
- 不新增 refresh。
- 不接 LLM。
- 不做全體交易統計。
- 不大規模重構。
- 只修本功能造成的問題。

完成後回報：

- 改了哪些檔案
- 哪些測試已跑
- 有哪些已知限制
- 後續可選功能有哪些
