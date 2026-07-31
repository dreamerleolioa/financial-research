export const PORTFOLIO_MUTATION_SCOPE = { id: "portfolio-state" } as const;

export const PORTFOLIO_MUTATION_REVISION_KEY = "portfolio_mutation_revision";
let fallbackRevision = "0";
let revisionSequence = 0;

export function readPortfolioMutationRevision(): string {
  try {
    return localStorage.getItem(PORTFOLIO_MUTATION_REVISION_KEY) ?? fallbackRevision;
  } catch {
    return fallbackRevision;
  }
}

export function markPortfolioMutationStarted(): string {
  revisionSequence += 1;
  const revision =
    typeof globalThis.crypto?.randomUUID === "function"
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${revisionSequence}`;
  fallbackRevision = revision;
  try {
    localStorage.setItem(PORTFOLIO_MUTATION_REVISION_KEY, revision);
  } catch {
    // The in-memory fallback still protects the active tab.
  }
  return revision;
}
