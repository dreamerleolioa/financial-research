export const activeEtfKeys = {
  all: ["active-etf-holdings"] as const,
  daily: (dataDate?: string) => [...activeEtfKeys.all, "daily", dataDate ?? "latest"] as const,
};
