import { useQuery } from "@tanstack/react-query";
import { fetchActiveEtfDaily, fetchActiveEtfRange } from "../../lib/activeEtfApi";
import { activeEtfKeys } from "./queryKeys";

export function useActiveEtfDailyQuery(dataDate?: string) {
  return useQuery({
    queryKey: activeEtfKeys.daily(dataDate),
    queryFn: () => fetchActiveEtfDaily(dataDate),
    retry: 1,
  });
}

export function useActiveEtfRangeQuery(start?: string, end?: string, symbol?: string, enabled = true) {
  return useQuery({
    queryKey: [...activeEtfKeys.all, "range", start ?? "default", end ?? "latest", symbol ?? "all"],
    queryFn: () => fetchActiveEtfRange(start, end, symbol),
    enabled,
    retry: 1,
  });
}
