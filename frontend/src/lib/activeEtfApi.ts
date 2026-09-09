import { requestJson } from "./apiClient";
import { activeEtfRangeResponseSchema, parseActiveEtfDailyResponse } from "./activeEtfSchemas";
import type { ActiveEtfDailyResponse } from "./activeEtfTypes";

export async function fetchActiveEtfDaily(dataDate?: string): Promise<ActiveEtfDailyResponse> {
  const response = await requestJson<unknown>("/active-etf-holdings/daily", {
    query: { data_date: dataDate },
  });
  return parseActiveEtfDailyResponse(response);
}

export async function fetchActiveEtfRange(startDate?: string, endDate?: string, symbol?: string) {
  const response = await requestJson<unknown>("/active-etf-holdings/range", {
    query: { start_date: startDate, end_date: endDate, symbol },
  });
  return activeEtfRangeResponseSchema.parse(response);
}
