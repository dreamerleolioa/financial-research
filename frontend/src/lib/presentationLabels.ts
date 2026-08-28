const DATA_MISSING_REASON_LABEL: Record<string, string> = {
  not_in_phase1_universe: "不在 AVWAP 管理範圍",
  phase1_snapshot_missing: "尚無 AVWAP 快照",
  phase1_snapshot_stale: "AVWAP 快照已過期",
  phase1_snapshot_read_failed: "AVWAP 快照讀取失敗",
  phase1_snapshot_bars_missing: "缺少計算 AVWAP 所需的價格資料",
  phase1_distance_to_avwap_missing: "暫時無法計算與 AVWAP 的距離",
  phase1_anchor_avwap_missing: "AVWAP 觀察線資料不完整",
  portfolio_current_price_missing: "缺少可用的最新價格",
  daily_price_history_unavailable: "缺少日價格歷史",
  daily_price_row_missing_for_data_date: "資料日缺少對應價格",
  twse_stock_day_request_failed: "交易所日價格讀取失敗",
  twse_stock_day_parser_error: "交易所日價格格式暫時無法處理",
  context_cache_missing: "尚無背景資料快照",
  context_cache_read_failed: "背景資料讀取失敗",
  context_payload_incomplete: "背景資料內容不完整",
  context_not_applicable_to_consumer: "此背景資料不適用於目前畫面",
  future_context_excluded: "已排除晚於分析日的資料",
  source_stale: "資料來源已過期",
  source_lagging: "資料來源尚未更新至分析日",
  provider_not_configured: "資料來源尚未設定",
  provider_deferred: "資料來源延後更新",
  provider_capacity_exhausted: "資料來源目前已達使用上限",
  provider_fetch_failed_or_empty: "資料來源讀取失敗或沒有資料",
  provider_coverage_below_fallback: "資料涵蓋不足，已使用備援資料",
  provider_coverage_insufficient: "資料涵蓋不足",
  unsupported_official_symbol: "官方資料來源不支援這個標的",
  unsupported_context_type: "不支援這類背景資料",
  official_no_data: "官方來源尚無資料",
  finmind_no_data: "市場資料來源尚無資料",
  finmind_access_required: "市場資料來源需要額外存取權限",
  tdcc_symbol_not_found: "集保資料查無這個標的",
  token_error: "資料來源授權失敗",
  capacity_exhausted: "資料來源目前已達使用上限",
  quota_exceeded: "資料來源目前已達使用上限",
  quota_or_token_error: "資料來源授權或使用額度異常",
  request_error: "資料來源請求失敗",
  response_error: "資料來源回應格式異常",
  api_error: "資料來源服務異常",
  missing_dependency: "資料讀取元件暫時不可用",
  candidate_price_history_missing: "個股價格歷史不足",
  benchmark_price_history_missing: "基準指數價格歷史不足",
  benchmark_data_date_missing: "基準指數缺少資料日期",
  benchmark_stale: "基準指數資料已過期",
  insufficient_aligned_history: "個股與基準指數可對齊的歷史資料不足",
  invalid_aligned_price_history: "個股與基準指數的對齊價格資料無效",
  market_index_ohlcv_missing: "大盤價格與成交量資料不足",
  market_index_stale: "大盤資料已過期",
  market_index_indicators_incomplete: "大盤技術指標資料不完整",
  market_index_volatility_date_invalid: "大盤波動資料日期無效",
  market_index_fetch_failed: "大盤資料讀取失敗",
};

const ANALYSIS_ERROR_MESSAGE: Record<string, string> = {
  ANALYZE_RUNTIME_ERROR: "分析暫時無法完成，請稍後再試。",
  CRAWL_ERROR: "無法取得這檔股票的市場資料，請稍後再試。",
  MISSING_SNAPSHOT: "缺少可用的市場快照，暫時無法完成分析。",
  NETWORK_ERROR: "目前無法連線到分析服務，請稍後再試。",
  RSS_FETCH_ERROR: "新聞資料暫時無法取得；其他可用資料仍會照常分析。",
};

export function formatDataMissingReason(
  reason: string | null | undefined,
  fallback = "資料暫時無法取得",
): string {
  if (!reason) return fallback;
  return DATA_MISSING_REASON_LABEL[reason] ?? fallback;
}

export function formatAnalysisError(error: { code?: string | null } | null | undefined): string {
  if (!error?.code) return "分析暫時無法完成，請稍後再試。";
  return ANALYSIS_ERROR_MESSAGE[error.code] ?? "分析暫時無法完成，請稍後再試。";
}

export function formatMarketDataset(dataset: string | null | undefined): string {
  if (dataset === "TaiwanStockPrice") return "台股日價格";
  return "市場價格資料";
}

export function formatPriceAdjustmentMode(mode: string | null | undefined): string {
  if (mode === "unadjusted") return "未還原價格";
  return "價格調整方式未標示";
}
