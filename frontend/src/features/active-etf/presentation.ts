import type { ActiveEtfAction, ActiveEtfConsensus } from "../../lib/activeEtfTypes";

export const ACTIVE_ETF_ACTION_LABEL: Record<ActiveEtfAction, string> = {
  added: "新增持股",
  increased: "持股增加",
  decreased: "持股減少",
  removed: "不再持有",
};

export const ACTIVE_ETF_ACTION_CLASS: Record<ActiveEtfAction, string> = {
  added: "bg-signal/15 text-signal",
  increased: "bg-positive/12 text-positive",
  decreased: "bg-negative/12 text-negative",
  removed: "bg-badge-neutral-bg text-badge-neutral-text",
};

export function getActiveEtfConsensusPresentation(consensus: ActiveEtfConsensus): {
  label: string;
  textClassName: string;
  badgeClassName: string;
  hasMultipleFunds: boolean;
  hasFundConsensus: boolean;
} {
  const hasMultipleFunds = consensus.fund_count >= 2;
  const hasFundConsensus = hasMultipleFunds && consensus.direction !== "mixed";
  if (consensus.direction === "increase") {
    return {
      label: hasFundConsensus ? "共同增加" : "單一基金增加",
      textClassName: "text-positive",
      badgeClassName: "bg-positive/12 text-positive",
      hasMultipleFunds,
      hasFundConsensus,
    };
  }
  if (consensus.direction === "decrease") {
    return {
      label: hasFundConsensus ? "共同減少" : "單一基金減少",
      textClassName: "text-negative",
      badgeClassName: "bg-negative/12 text-negative",
      hasMultipleFunds,
      hasFundConsensus,
    };
  }
  return {
    label: "方向分歧",
    textClassName: "text-signal",
    badgeClassName: "bg-signal/15 text-signal",
    hasMultipleFunds,
    hasFundConsensus,
  };
}
