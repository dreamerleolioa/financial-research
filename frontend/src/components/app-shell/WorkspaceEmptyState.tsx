import type { ReactNode } from "react";

export function WorkspaceEmptyState({
  eyebrow,
  title,
  description,
  actions,
  meta,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <section className="grid gap-5 border-y border-border py-7 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
      <div className="max-w-2xl">
        {eyebrow && (
          <p className="text-[0.6875rem] font-semibold tracking-[0.14em] text-accent uppercase">{eyebrow}</p>
        )}
        <h2 className={`${eyebrow ? "mt-2" : ""} text-lg font-semibold text-text-primary`}>{title}</h2>
        <p className="mt-2 text-sm leading-relaxed text-text-muted">{description}</p>
        {meta && <div className="mt-3 text-xs leading-relaxed text-text-faint">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2 sm:justify-end">{actions}</div>}
    </section>
  );
}
