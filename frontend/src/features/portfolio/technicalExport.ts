import { formatPrice } from "../../lib/formatters";
import { buildTechnicalIndicatorsCopyText, type TechnicalIndicatorsCopyPayload } from "../../lib/technicalIndicators";

export interface PortfolioTechnicalPositionContext {
  symbol: string;
  entryPrice: number;
  entryDate: string;
  quantity: number;
}

export interface PortfolioTechnicalExportEntry {
  position: PortfolioTechnicalPositionContext;
  technical: TechnicalIndicatorsCopyPayload;
  snapshot: Record<string, unknown>;
}

interface PortfolioTechnicalExportInput {
  entries: PortfolioTechnicalExportEntry[];
}

function buildPositionContextRows({ position }: PortfolioTechnicalExportEntry): string[] {
  return [
    `持股成本：${formatPrice(position.entryPrice, position.symbol)}`,
    `進場日期：${position.entryDate}`,
    `持有股數：${position.quantity}`,
  ];
}

export function buildPortfolioTechnicalCopyText({ entries }: PortfolioTechnicalExportInput): string {
  return entries
    .map((entry) => {
      const [heading, ...technicalRows] = buildTechnicalIndicatorsCopyText(entry.technical, entry.snapshot).split("\n");
      return [heading, ...buildPositionContextRows(entry), ...technicalRows].join("\n");
    })
    .join("\n\n---\n\n");
}
