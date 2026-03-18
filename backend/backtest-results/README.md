# 新倉策略回測結果

此目錄存放每次執行 `backtest_win_rate.py --mode new-position` 的 JSON 輸出。

## 執行指令

```bash
cd backend
python3 scripts/backtest_win_rate.py \
  --mode new-position \
  --days 90 \
  --require-final-raw-data \
  --output-json backtest-results/new-position-baseline-$(date +%Y%m%d).json
```

## 回測紀錄

| 執行日期 | 檔案 | strategy_version | 樣本數（5日） | 整體勝率（5日） | mid_term 勝率（5日） | high conviction 勝率（5日） | signal_confidence r | 備注 |
|---|---|---|---|---|---|---|---|---|
| （待首次執行後填入） | - | - | - | - | - | - | - | - |

## 解讀準則

| 勝率範圍 | 解讀 |
|---|---|
| > 60% | 策略在此分箱有預測價值 |
| 50–60% | 邊際有效，需持續累積樣本觀察 |
| < 50% | 策略邏輯可能有問題，需人工審核 |

- 分箱 n < 10：不可靠，僅供觀察
- 分箱 n 10–29：初步參考，謹慎解讀
- 分箱 n >= 30：可初步得出結論

## 分箱一致性預期

- `mid_term` 勝率應高於 `short_term`
- `high` conviction 勝率應高於 `medium` > `low`
- `evidence_scores.total >= 4` 勝率應高於 `total < 2`
