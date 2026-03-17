export function getTaiwanTickSize(price: number): number {
  if (price < 10) return 0.01;
  if (price < 50) return 0.05;
  if (price < 100) return 0.1;
  if (price < 500) return 0.5;
  if (price < 1000) return 1;
  return 5;
}

function decimalPlaces(step: number): number {
  const stepText = step.toString();
  const dotIndex = stepText.indexOf(".");
  return dotIndex === -1 ? 0 : stepText.length - dotIndex - 1;
}

export function formatPrice(value: number | null | undefined, symbol?: string): string {
  if (value == null || Number.isNaN(value)) return "—";
  const symbolText = (symbol ?? "").toUpperCase();
  const isTaiwanStock = symbolText.endsWith(".TW") || symbolText.endsWith(".TWO");
  if (isTaiwanStock && value > 0) {
    const tick = getTaiwanTickSize(value);
    const normalized = Math.round((value + Number.EPSILON) / tick) * tick;
    return normalized.toFixed(decimalPlaces(tick));
  }
  return new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: 6 }).format(value);
}

export function formatVolume(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("zh-TW").format(value);
}
