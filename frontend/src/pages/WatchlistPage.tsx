import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import { analyzeSymbol } from "../lib/analyzeApi";
import type { AnalyzeResponse } from "../lib/analysisTypes";
import { formatPrice, formatVolume } from "../lib/formatters";
import {
  buildTechnicalIndicatorsCopyText,
  COPY_STATUS_RESET_MS,
  formatIndicatorNumber,
  formatMovingAverages,
  getTechnicalIndicatorLabel,
  type CopyStatus,
  writeClipboardText,
} from "../lib/technicalIndicators";
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

interface WatchlistTechnicalState {
  loading: boolean;
  expanded: boolean;
  error: string | null;
  result: AnalyzeResponse | null;
  copyStatus: CopyStatus;
}

const EMPTY_TECHNICAL_STATE: WatchlistTechnicalState = {
  loading: false,
  expanded: false,
  error: null,
  result: null,
  copyStatus: "idle",
};

function WatchlistTechnicalPanel({
  item,
  technicalState,
  listBusy,
  onRefresh,
  onCopy,
}: {
  item: WatchlistItem;
  technicalState: WatchlistTechnicalState;
  listBusy: boolean;
  onRefresh: () => void;
  onCopy: () => void;
}) {
  const quickResult = technicalState.result;
  const quickSnapshot = quickResult?.snapshot ?? {};
  const quickSnapshotSymbol = typeof quickSnapshot.symbol === "string" ? quickSnapshot.symbol : item.symbol;
  const quickIndicators = quickResult?.technical_indicators ?? null;
  const quickSessionLabel = quickResult?.is_final === false ? "盤中" : "收盤";
  const pricePair = (first: number | null | undefined, second: number | null | undefined, emptyLabel = "—") =>
    first != null || second != null
      ? `${formatPrice(first, quickSnapshotSymbol)} / ${formatPrice(second, quickSnapshotSymbol)}`
      : emptyLabel;
  const indicatorRows = quickIndicators
    ? [
        ["現價", formatPrice(quickSnapshot.current_price as number | null | undefined, quickSnapshotSymbol)],
        ["成交量", formatVolume(quickSnapshot.volume)],
        ["均線 MA5／20／60", formatMovingAverages(quickIndicators, quickSnapshotSymbol)],
        ["20 日最高／最低", pricePair(quickIndicators.high_20d, quickIndicators.low_20d)],
        ["60 日最高／最低", pricePair(quickIndicators.high_60d, quickIndicators.low_60d, "資料不足")],
        ["布林通道", getTechnicalIndicatorLabel("bollinger_position", quickIndicators.bollinger_position)],
        ["MACD 方向", getTechnicalIndicatorLabel("macd_bias", quickIndicators.macd_bias)],
        [
          "KD",
          `${getTechnicalIndicatorLabel("kd_zone", quickIndicators.kd_zone)} / ${getTechnicalIndicatorLabel("kd_signal", quickIndicators.kd_signal)}（K/D ${formatIndicatorNumber(quickIndicators.kd_k, 1)} / ${formatIndicatorNumber(quickIndicators.kd_d, 1)}）`,
        ],
        [
          "ADX",
          `${getTechnicalIndicatorLabel("adx_trend_strength", quickIndicators.adx_trend_strength)} / ${getTechnicalIndicatorLabel("adx_trend_direction", quickIndicators.adx_trend_direction)}（${formatIndicatorNumber(quickIndicators.adx, 1)}）`,
        ],
        [
          "OBV",
          `${getTechnicalIndicatorLabel("obv_signal", quickIndicators.obv_signal)} / ${getTechnicalIndicatorLabel("obv_trend", quickIndicators.obv_trend_20d)}`,
        ],
        [
          "ATR / ATR%",
          `${formatIndicatorNumber(quickIndicators.atr, 2)} / ${formatIndicatorNumber(quickIndicators.atr_pct, 2)}%`,
        ],
        [
          "MFI",
          `${formatIndicatorNumber(quickIndicators.mfi, 1)} / ${getTechnicalIndicatorLabel("mfi_signal", quickIndicators.mfi_signal)}`,
        ],
        [
          "唐奇安通道",
          `${getTechnicalIndicatorLabel("donchian_position", quickIndicators.donchian_position)}（${formatIndicatorNumber(quickIndicators.donchian_upper, 2)} / ${formatIndicatorNumber(quickIndicators.donchian_lower, 2)}）`,
        ],
      ]
    : [];

  return (
    <div className="rounded-lg border border-border bg-card-hover/50 p-4 md:col-span-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-text-primary">技術指標快查</p>
          <p className="mt-1 text-xs text-text-muted">
            {quickResult ? `${quickSessionLabel}資料，不執行完整 AI 分析` : "讀取技術指標中"}
            {technicalState.loading && quickResult ? "，更新中" : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onRefresh}
            disabled={listBusy || technicalState.loading}
            className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-text-secondary transition hover:bg-card disabled:cursor-not-allowed disabled:opacity-50"
          >
            更新
          </button>
          <button
            type="button"
            onClick={onCopy}
            disabled={!quickResult}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              technicalState.copyStatus === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                : technicalState.copyStatus === "error"
                  ? "border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
                  : "border-border bg-card text-text-secondary hover:border-indigo-200 hover:text-indigo-600 dark:hover:border-indigo-700 dark:hover:text-indigo-300"
            }`}
            aria-label={`複製 ${getDisplayName(item)} 技術指標`}
          >
            {technicalState.copyStatus === "success"
              ? "已複製"
              : technicalState.copyStatus === "error"
                ? "複製失敗"
                : "複製指標"}
          </button>
        </div>
      </div>

      {technicalState.loading && !quickResult && (
        <div className="flex items-center gap-2 py-3 text-sm text-text-muted">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-100 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400" />
          正在讀取技術指標
        </div>
      )}

      {technicalState.error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {technicalState.error}
        </div>
      )}

      {quickResult && !quickIndicators && !technicalState.error && (
        <div className="rounded-md border border-border bg-card px-3 py-2 text-sm text-text-muted">
          技術指標資料不足，請稍後更新。
        </div>
      )}

      {quickIndicators && (
        <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {indicatorRows.map(([label, value]) => (
            <div key={label}>
              <p className="mb-1 text-xs text-text-muted">{label}</p>
              <p className="text-sm font-medium text-text-primary">{value}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
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
  const [technicalStateByItemId, setTechnicalStateByItemId] = useState<Record<number, WatchlistTechnicalState>>({});
  const [error, setError] = useState<string | null>(null);
  const technicalCopyResetTimersRef = useRef<Record<number, number>>({});

  const itemCount = items.length;
  const sortedItems = useMemo(
    () =>
      [...items].sort((a, b) => a.sort_order - b.sort_order || b.created_at.localeCompare(a.created_at) || b.id - a.id),
    [items],
  );
  const visibleItems = useMemo(() => {
    if (!dragPreviewIds) return sortedItems;

    const itemById = new Map(sortedItems.map((item) => [item.id, item]));
    const previewItems = dragPreviewIds
      .map((id) => itemById.get(id))
      .filter((item): item is WatchlistItem => Boolean(item));
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

  useEffect(() => {
    return () => {
      Object.values(technicalCopyResetTimersRef.current).forEach((timerId) => window.clearTimeout(timerId));
    };
  }, []);

  function getTechnicalState(itemId: number): WatchlistTechnicalState {
    return technicalStateByItemId[itemId] ?? EMPTY_TECHNICAL_STATE;
  }

  function updateTechnicalState(itemId: number, updater: (state: WatchlistTechnicalState) => WatchlistTechnicalState) {
    setTechnicalStateByItemId((current) => {
      const previous = current[itemId] ?? EMPTY_TECHNICAL_STATE;
      return { ...current, [itemId]: updater(previous) };
    });
  }

  function updateTechnicalCopyStatus(itemId: number, status: CopyStatus) {
    updateTechnicalState(itemId, (state) => ({ ...state, copyStatus: status }));

    const previousTimerId = technicalCopyResetTimersRef.current[itemId];
    if (previousTimerId) window.clearTimeout(previousTimerId);

    if (status !== "idle") {
      technicalCopyResetTimersRef.current[itemId] = window.setTimeout(() => {
        updateTechnicalState(itemId, (state) => ({ ...state, copyStatus: "idle" }));
        delete technicalCopyResetTimersRef.current[itemId];
      }, COPY_STATUS_RESET_MS);
    }
  }

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

  async function handleQuickTechnicalLookup(item: WatchlistItem, forceRefresh = false) {
    const currentState = getTechnicalState(item.id);
    if (currentState.loading) return;

    if (currentState.result && !forceRefresh) {
      updateTechnicalState(item.id, (state) => ({ ...state, expanded: !state.expanded, error: null }));
      return;
    }

    updateTechnicalState(item.id, (state) => ({
      ...state,
      loading: true,
      expanded: true,
      error: null,
      copyStatus: "idle",
    }));

    try {
      const result = await analyzeSymbol({ symbol: item.symbol, skip_ai: true });
      updateTechnicalState(item.id, (state) => ({
        ...state,
        loading: false,
        expanded: true,
        error: null,
        result,
      }));
    } catch (err) {
      updateTechnicalState(item.id, (state) => ({
        ...state,
        loading: false,
        expanded: true,
        error: err instanceof Error ? err.message : "技術指標快查失敗",
      }));
    }
  }

  async function handleCopyTechnicalIndicators(item: WatchlistItem) {
    const technicalState = getTechnicalState(item.id);
    if (!technicalState.result) return;

    try {
      await writeClipboardText(buildTechnicalIndicatorsCopyText(technicalState.result, technicalState.result.snapshot));
      updateTechnicalCopyStatus(item.id, "success");
    } catch {
      updateTechnicalCopyStatus(item.id, "error");
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
      setTechnicalStateByItemId((current) => {
        const next = { ...current };
        delete next[item.id];
        return next;
      });
      const resetTimerId = technicalCopyResetTimersRef.current[item.id];
      if (resetTimerId) {
        window.clearTimeout(resetTimerId);
        delete technicalCopyResetTimersRef.current[item.id];
      }
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

        <form
          onSubmit={handleAddItem}
          className="grid gap-3 md:grid-cols-[minmax(160px,220px)_minmax(0,1fr)_auto] md:items-start"
        >
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
              const technicalState = getTechnicalState(item.id);
              const quickResult = technicalState.result;

              return (
                <article
                  key={item.id}
                  data-watchlist-item-id={item.id}
                  className={`grid gap-4 px-4 py-4 transition md:grid-cols-[auto_minmax(160px,220px)_minmax(0,1fr)_auto] md:items-start md:px-6 ${
                    isDragging
                      ? "bg-indigo-50/70 opacity-70 ring-2 ring-inset ring-indigo-300 dark:bg-indigo-950/30 dark:ring-indigo-700"
                      : ""
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
                    <button
                      type="button"
                      onClick={() => void handleQuickTechnicalLookup(item)}
                      disabled={listBusy || technicalState.loading}
                      className="rounded-lg bg-indigo-600 px-3 py-2 text-center text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {technicalState.loading
                        ? "快查中..."
                        : quickResult && technicalState.expanded
                          ? "收合指標"
                          : "技術快查"}
                    </button>
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

                  {technicalState.expanded && (
                    <WatchlistTechnicalPanel
                      item={item}
                      technicalState={technicalState}
                      listBusy={listBusy}
                      onRefresh={() => void handleQuickTechnicalLookup(item, true)}
                      onCopy={() => void handleCopyTechnicalIndicators(item)}
                    />
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
