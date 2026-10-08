import { useQuery } from "@tanstack/react-query";
import {
  fetchDailyRadarValidation,
  fetchLatestDailyRadarRun,
  isNoPublicDailyRadarRunUnavailableError,
} from "../../lib/dailyRadarApi";

export const dailyRadarKeys = {
  latest: ["daily-radar", "latest"] as const,
  validation: ["daily-radar", "validation"] as const,
};

export function useDailyRadarValidationQuery(enabled: boolean, lookbackDays = 90) {
  return useQuery({
    queryKey: [...dailyRadarKeys.validation, lookbackDays],
    queryFn: ({ signal }) => fetchDailyRadarValidation(signal, lookbackDays),
    enabled,
    staleTime: 60_000,
    retry: 1,
  });
}

export function useLatestDailyRadarQuery() {
  return useQuery({
    queryKey: dailyRadarKeys.latest,
    queryFn: async ({ signal }) => {
      try {
        return await fetchLatestDailyRadarRun({}, signal);
      } catch (error) {
        if (isNoPublicDailyRadarRunUnavailableError(error)) return null;
        throw error;
      }
    },
    staleTime: 60_000,
    retry: 1,
  });
}
