import { Link } from "react-router-dom";

export function ProductBrand({
  compact = false,
  to = "/analyze",
}: {
  compact?: boolean;
  to?: string;
}) {
  return (
    <Link to={to} className="group flex min-w-0 items-center gap-3 rounded-[10px]">
      <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] border border-accent/30 bg-accent-soft text-sm font-semibold text-accent shadow-panel">
        研
        <span className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-signal" aria-hidden="true" />
      </span>
      <span className="min-w-0 leading-tight">
        <span className="block truncate text-sm font-semibold text-text-primary">個股研究台</span>
        {!compact && (
          <span className="mt-1 block text-[0.625rem] font-medium tracking-[0.16em] text-text-faint uppercase">
            Taiwan Research Desk
          </span>
        )}
      </span>
    </Link>
  );
}
