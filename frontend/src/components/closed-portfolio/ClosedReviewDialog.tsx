import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface ClosedReviewDialogProps {
  title: string;
  description: string;
  currentPosition: number;
  totalCount: number;
  canGoPrevious: boolean;
  canGoNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onClose: () => void;
  children: ReactNode;
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function ClosedReviewDialog({
  title,
  description,
  currentPosition,
  totalCount,
  canGoPrevious,
  canGoNext,
  onPrevious,
  onNext,
  onClose,
  children,
}: ClosedReviewDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const appRoot = document.getElementById("root");
    const previousRootInert = appRoot?.inert ?? false;
    const previousRootAriaHidden = appRoot?.getAttribute("aria-hidden") ?? null;
    document.body.style.overflow = "hidden";
    if (appRoot) {
      appRoot.inert = true;
      appRoot.setAttribute("aria-hidden", "true");
    }
    closeButtonRef.current?.focus({ preventScroll: true });

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const panel = panelRef.current;
      if (!panel) return;
      const focusableElements = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (element) =>
          !element.hasAttribute("disabled") &&
          element.getAttribute("aria-hidden") !== "true" &&
          element.getClientRects().length > 0,
      );
      if (focusableElements.length === 0) {
        event.preventDefault();
        panel.focus({ preventScroll: true });
        return;
      }

      const firstElement = focusableElements[0];
      const lastElement = focusableElements[focusableElements.length - 1];
      if (event.shiftKey && document.activeElement === firstElement) {
        event.preventDefault();
        lastElement.focus();
      } else if (!event.shiftKey && document.activeElement === lastElement) {
        event.preventDefault();
        firstElement.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (appRoot) {
        appRoot.inert = previousRootInert;
        if (previousRootAriaHidden === null) appRoot.removeAttribute("aria-hidden");
        else appRoot.setAttribute("aria-hidden", previousRootAriaHidden);
      }
    };
  }, []);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 p-0 backdrop-blur-[1px] sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        className="grid max-h-[100dvh] w-full grid-rows-[auto_minmax(0,1fr)] overflow-hidden rounded-t-[16px] border border-border bg-surface-raised shadow-panel outline-none sm:max-h-[calc(100dvh-2rem)] sm:max-w-[1440px] sm:rounded-[16px]"
      >
        <header className="border-b border-border-subtle bg-surface-raised px-4 py-3 sm:px-5 sm:py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h2 id={titleId} className="truncate text-base font-semibold text-text-primary">
                  {title}
                </h2>
                <span className="shrink-0 rounded-md bg-badge-neutral-bg px-2 py-0.5 text-xs font-medium tabular-nums text-badge-neutral-text">
                  {currentPosition} / {totalCount}
                </span>
              </div>
              <p id={descriptionId} className="mt-1 text-xs text-text-faint">
                {description}
              </p>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              className="ui-icon-button shrink-0 border border-border"
              aria-label="關閉完整交易復盤"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-5 w-5"
                viewBox="0 0 20 20"
                fill="currentColor"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                  clipRule="evenodd"
                />
              </svg>
            </button>
          </div>

          <nav className="mt-3 flex items-center gap-2" aria-label="切換完整交易">
            <button
              type="button"
              onClick={onPrevious}
              disabled={!canGoPrevious}
              className="ui-button-secondary min-h-9 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-45"
            >
              上一筆完整交易
            </button>
            <button
              type="button"
              onClick={onNext}
              disabled={!canGoNext}
              className="ui-button-secondary min-h-9 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-45"
            >
              下一筆完整交易
            </button>
          </nav>
        </header>

        <div
          id="closed-review-workspace"
          tabIndex={-1}
          className="min-h-0 overscroll-contain overflow-y-auto p-4 sm:p-5"
        >
          <div className="space-y-4">{children}</div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
