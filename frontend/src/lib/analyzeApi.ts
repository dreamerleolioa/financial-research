import { requestJson } from "./apiClient";
import { parseAnalyzeResponse } from "./analysisSchemas";
import type { AnalyzeResponse } from "./analysisTypes";

export interface AnalyzeRequest {
  symbol: string;
  skip_ai?: boolean;
}

export async function analyzeSymbol(body: AnalyzeRequest, signal?: AbortSignal): Promise<AnalyzeResponse> {
  const data = await requestJson<unknown>("/analyze", {
    method: "POST",
    body,
    signal,
  });
  return parseAnalyzeResponse(data);
}
