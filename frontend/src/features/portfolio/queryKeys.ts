export const portfolioKeys = {
  all: ["portfolio"] as const,
  items: () => [...portfolioKeys.all, "items"] as const,
  riskSummary: () => [...portfolioKeys.all, "risk-summary"] as const,
  latestHistory: () => [...portfolioKeys.all, "latest-history"] as const,
  decisionContext: () => [...portfolioKeys.all, "decision-context"] as const,
  history: (id: number) => [...portfolioKeys.all, "history", id] as const,
  lifecyclePlan: (id: number) => [...portfolioKeys.all, "lifecycle-plan", id] as const,
};
