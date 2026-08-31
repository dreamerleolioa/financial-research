import { requestJson } from "./apiClient";
import { parseActiveEtfDailyResponse } from "./activeEtfSchemas";
import type { ActiveEtfDailyResponse } from "./activeEtfTypes";

export async function fetchActiveEtfDaily(dataDate?: string): Promise<ActiveEtfDailyResponse> {
  const response = await requestJson<unknown>("/active-etf-holdings/daily", {
    query: { data_date: dataDate },
  });
  return parseActiveEtfDailyResponse(response);
}
