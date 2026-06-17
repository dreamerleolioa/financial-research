import { useEffect, useMemo, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router-dom";
import {
  createWatchlistItem,
  deleteWatchlistItem,
  fetchWatchlistItems,
  reorderWatchlistItems,
  updateWatchlistItem,
  type WatchlistItem,
} from "../lib/watchlistApi";

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getDisplayName(item: WatchlistItem): string {
  return item.name ? `${item.name} ${item.symbol}` : item.symbol;
}

function moveItem<T>(items: T[], fromIndex: number, toIndex: number): T[] {
  const nextItems = [...items];
  const [movedItem] = nextItems.splice(fromIndex, 1);
  nextItems.splice(toIndex, 0, movedItem);
  return nextItems;
}

function haveSameOrder(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [symbol, setSymbol] = useState("");
  const [notes, setNotes] = useState("");
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyItemId, setBusyItemId] = useState<number | null>(null);
  const [reordering, setReordering] = useState(false);
  const [draggedItemId, setDraggedItemId] = useState<number | null>(null);
  const [dragOverItemId, setDragOverItemId] = useState<number | null>(null);
  const [dragPreviewIds, setDragPreviewIds] = useState<number[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const itemCount = items.length;
  const sortedItems = useMemo(
    () => [...items].sort((a, b) => a.sort_order - b.sort_order || b.created_at.localeCompare(a.created_at) || b.id - a.id),
    [items],
  );
  const visibleItems = useMemo(() => {
    if (!dragPreviewIds) return sortedItems;

    const itemById = new Map(sortedItems.map((item) => [item.id, item]));
    const previewItems = dragPreviewIds.map((id) => itemById.get(id)).filter((item): item is WatchlistItem => Boolean(item));
    return previewItems.length === sortedItems.length ? previewItems : sortedItems;
  }, [dragPreviewIds, sortedItems]);

  async function refreshWatchlist() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWatchlistItems();
      setItems(data);
      setNoteDrafts(Object.fromEntries(data.map((item) => [item.id, item.notes ?? ""])));
    } catch (err) {
      setError(err instanceof Error ? err.message : "無法讀取關注列表");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshWatchlist();
  }, []);

  async function handleAddItem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!symbol.trim()) return;

    setSaving(true);
    setError(null);
    try {
      const trimmedNotes = notes.trim();
      await createWatchlistItem({
        symbol: symbol.trim(),
        ...(trimmedNotes ? { notes: trimmedNotes } : {}),
      });
      setSymbol("");
      setNotes("");
      await refreshWatchlist();
    } catch (err) {
      setError(err instanceof Error ? err.message : "新增關注項目失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveNotes(item: WatchlistItem) {
    setBusyItemId(item.id);
    setError(null);
    try {
      const updated = await updateWatchlistItem(item.id, {
        notes: noteDrafts[item.id]?.trim() || null,
      });
      setItems((current) => current.map((row) => (row.id === updated.id ? updated : row)));
      setNoteDrafts((current) => ({ ...current, [updated.id]: updated.notes ?? "" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新備註失敗");
    } finally {
      setBusyItemId(null);
    }
  }

  async function persistWatchlistOrder(itemIds: number[]) {
    const previousItems = items;

    setReordering(true);
    setError(null);
    setItems((currentItems) => {
      const itemById = new Map(currentItems.map((item) => [item.id, item]));
      const orderedItems = itemIds.map((id, index) => {
        const item = itemById.get(id);
        return item ? { ...item, sort_order: index } : null;
      });
      return orderedItems.every(Boolean) ? (orderedItems as WatchlistItem[]) : currentItems;
    });

    try {
      const updatedItems = await reorderWatchlistItems({
        item_ids: itemIds,
      });
      setItems(updatedItems);
    } catch (err) {
      setItems(previousItems);
      setError(err instanceof Error ? err.message : "調整排序失敗");
    } finally {
      setReordering(false);
    }
  }

  function previewMoveToItem(targetItemId: number, activeDraggedItemId: number, currentOrder: number[]): number[] {
    if (activeDraggedItemId === targetItemId || reordering || busyItemId !== null) return currentOrder;

    const fromIndex = currentOrder.indexOf(activeDraggedItemId);
    const toIndex = currentOrder.indexOf(targetItemId);
    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return currentOrder;

    const nextOrder = moveItem(currentOrder, fromIndex, toIndex);
    setDragPreviewIds(nextOrder);
    setDragOverItemId(targetItemId);
    return nextOrder;
  }

  function getItemIdAtPoint(clientX: number, clientY: number): number | null {
    const element = document.elementFromPoint(clientX, clientY);
    const itemElement = element?.closest("[data-watchlist-item-id]");
    if (!(itemElement instanceof HTMLElement)) return null;

    const itemId = Number(itemElement.dataset.watchlistItemId);
    return Number.isFinite(itemId) ? itemId : null;
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLButtonElement>, item: WatchlistItem) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (reordering || busyItemId !== null) {
      event.preventDefault();
      return;
    }

    event.preventDefault();
    const startOrder = sortedItems.map((row) => row.id);
    let previewOrder = startOrder;

    function cleanupListeners() {
      window.removeEventListener("pointermove", handleGlobalPointerMove);
      window.removeEventListener("pointerup", handleGlobalPointerEnd);
      window.removeEventListener("pointercancel", handleGlobalPointerCancel);
    }

    function handleGlobalPointerMove(globalEvent: globalThis.PointerEvent) {
      if (globalEvent.pointerId !== event.pointerId) return;
      globalEvent.preventDefault();

      const targetItemId = getItemIdAtPoint(globalEvent.clientX, globalEvent.clientY);
      if (targetItemId == null) return;
      previewOrder = previewMoveToItem(targetItemId, item.id, previewOrder);
    }

    function handleGlobalPointerEnd(globalEvent: globalThis.PointerEvent) {
      if (globalEvent.pointerId !== event.pointerId) return;
      globalEvent.preventDefault();
      cleanupListeners();
      resetDragState();

      if (haveSameOrder(previewOrder, startOrder)) return;
      void persistWatchlistOrder(previewOrder);
    }

    function handleGlobalPointerCancel(globalEvent: globalThis.PointerEvent) {
      if (globalEvent.pointerId !== event.pointerId) return;
      globalEvent.preventDefault();
      cleanupListeners();
      resetDragState();
    }

    window.addEventListener("pointermove", handleGlobalPointerMove, { passive: false });
    window.addEventListener("pointerup", handleGlobalPointerEnd, { passive: false });
    window.addEventListener("pointercancel", handleGlobalPointerCancel, { passive: false });
    setDraggedItemId(item.id);
    setDragOverItemId(item.id);
    setDragPreviewIds(startOrder);
  }

  function resetDragState() {
    setDraggedItemId(null);
    setDragOverItemId(null);
    setDragPreviewIds(null);
  }

  async function handleDeleteItem(item: WatchlistItem) {
    setBusyItemId(item.id);
    setError(null);
    try {
      await deleteWatchlistItem(item.id);
      setItems((current) => current.filter((row) => row.id !== item.id));
      setNoteDrafts((current) => {
        const next = { ...current };
        delete next[item.id];
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "移除關注項目失敗");
    } finally {
      setBusyItemId(null);
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm md:p-6">
        <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-text-primary">關注列表</h2>
            <p className="mt-1 text-xs text-text-muted">儲存還沒進入持股的觀察標的，之後可直接帶入個股分析。</p>
          </div>
          <span className="text-xs text-text-faint">{itemCount} 檔關注中</span>
        </div>

        <form onSubmit={handleAddItem} className="grid gap-3 md:grid-cols-[minmax(160px,220px)_minmax(0,1fr)_auto] md:items-start">
          <label className="space-y-1">
            <span className="text-xs font-medium text-text-muted">股票代碼</span>
            <input
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
              placeholder="例如 2330.TW"
              disabled={saving}
              className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 disabled:opacity-60 dark:ring-indigo-500"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-text-muted">觀察備註</span>
            <input
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="例如：等量縮回測 MA20"
              disabled={saving}
              className="w-full rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 disabled:opacity-60 dark:ring-indigo-500"
            />
          </label>
          <button
            type="submit"
            disabled={saving || !symbol.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60 md:mt-5"
          >
            {saving ? "儲存中..." : "加入關注"}
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-border bg-card shadow-sm">
        {loading ? (
          <div className="flex items-center justify-center gap-3 px-4 py-12 text-sm text-text-muted">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" />
            讀取關注列表中
          </div>
        ) : visibleItems.length === 0 ? (
          <div className="px-4 py-12 text-center">
            <p className="text-sm font-medium text-text-primary">尚未加入關注股票</p>
            <p className="mt-1 text-xs text-text-muted">從上方輸入股票代碼，或在個股分析結果中直接加入關注。</p>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {visibleItems.map((item) => {
              const noteDraft = noteDrafts[item.id] ?? "";
              const noteChanged = noteDraft !== (item.notes ?? "");
              const itemBusy = busyItemId === item.id;
              const listBusy = reordering || busyItemId !== null;
              const isDragging = draggedItemId === item.id;
              const isDropTarget = dragOverItemId === item.id && draggedItemId !== item.id;

              return (
                <article
                  key={item.id}
                  data-watchlist-item-id={item.id}
                  className={`grid gap-4 px-4 py-4 transition md:grid-cols-[auto_minmax(160px,220px)_minmax(0,1fr)_auto] md:items-start md:px-6 ${
                    isDragging ? "bg-indigo-50/70 opacity-70 ring-2 ring-inset ring-indigo-300 dark:bg-indigo-950/30 dark:ring-indigo-700" : ""
                  } ${isDropTarget ? "bg-card-hover" : ""}`}
                >
                  <div className="flex gap-1 md:flex-col" aria-label={`${getDisplayName(item)} 排序控制`}>
                    <button
                      type="button"
                      onPointerDown={(event) => handlePointerDown(event, item)}
                      disabled={listBusy}
                      title="拖拉排序"
                      aria-label={`拖拉排序 ${getDisplayName(item)}`}
                      className="grid h-8 w-8 touch-none cursor-grab place-items-center rounded-lg border border-border text-sm font-semibold text-text-secondary transition hover:bg-card-hover active:cursor-grabbing disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      ⋮⋮
                    </button>
                  </div>

                  <div>
                    <p className="text-sm font-semibold text-text-primary">{getDisplayName(item)}</p>
                    <p className="mt-1 text-xs text-text-faint">加入於 {formatDateTime(item.created_at)}</p>
                  </div>

                  <textarea
                    value={noteDraft}
                    onChange={(event) => setNoteDrafts((current) => ({ ...current, [item.id]: event.target.value }))}
                    rows={2}
                    placeholder="補充觀察條件"
                    disabled={itemBusy}
                    className="min-h-16 w-full resize-y rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 disabled:opacity-60 dark:ring-indigo-500"
                  />

                  <div className="flex flex-wrap justify-end gap-2 md:flex-col md:items-stretch">
                    <Link
                      to={`/analyze?symbol=${encodeURIComponent(item.symbol)}`}
                      className="rounded-lg bg-indigo-600 px-3 py-2 text-center text-sm font-medium text-white transition hover:bg-indigo-700"
                    >
                      查詢
                    </Link>
                    <button
                      type="button"
                      onClick={() => void handleSaveNotes(item)}
                      disabled={listBusy || !noteChanged}
                      className="rounded-lg border border-border px-3 py-2 text-sm font-medium text-text-secondary transition hover:bg-card-hover disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      儲存備註
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDeleteItem(item)}
                      disabled={listBusy}
                      className="rounded-lg border border-red-200 px-3 py-2 text-sm font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
                    >
                      移除
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
