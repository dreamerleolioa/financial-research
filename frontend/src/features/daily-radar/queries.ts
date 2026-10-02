import { useQuery } from "@tanstack/react-query";
import { fetchLatestDailyRadarRun, isNoPublicDailyRadarRunUnavailableError } from "../../lib/dailyRadarApi";

export const dailyRadarKeys = {
  latest: ["daily-radar", "latest"] as const,
};

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
