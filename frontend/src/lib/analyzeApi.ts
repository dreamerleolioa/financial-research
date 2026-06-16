import { requestJson } from "./apiClient";
import type { AnalyzeResponse } from "./analysisTypes";

export interface AnalyzeRequest {
  symbol: string;
  skip_ai?: boolean;
}

export function analyzeSymbol(body: AnalyzeRequest, signal?: AbortSignal): Promise<AnalyzeResponse> {
  return requestJson<AnalyzeResponse>("/analyze", {
    method: "POST",
    body,
    signal,
  });
}

