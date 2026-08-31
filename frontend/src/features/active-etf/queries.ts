import { useQuery } from "@tanstack/react-query";
import { fetchActiveEtfDaily } from "../../lib/activeEtfApi";
import { activeEtfKeys } from "./queryKeys";

export function useActiveEtfDailyQuery(dataDate?: string) {
  return useQuery({
    queryKey: activeEtfKeys.daily(dataDate),
    queryFn: () => fetchActiveEtfDaily(dataDate),
    retry: 1,
  });
}
