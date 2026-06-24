import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import { analyzeSymbol } from "../lib/analyzeApi";
import type { AnalyzeResponse, Phase1Observation } from "../lib/analysisTypes";
import { TechnicalIndicatorsPanel, TechnicalProfileDisclosure } from "../components/TechnicalIndicatorsPanel";
import { formatPrice } from "../lib/formatters";
import {
  buildTechnicalIndicatorsCopyText,
  COPY_STATUS_RESET_MS,
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

const PHASE1_ANCHOR_ORDER = ["swing_low_60d", "breakout_20d", "high_volume_60d", "entry"] as const;

const PHASE1_ANCHOR_LABEL: Record<string, string> = {
  swing_low_60d: "60 日波段低點",
  breakout_20d: "20 日突破",
  high_volume_60d: "60 日大量",
  entry: "持股進場日",
};

const PHASE1_MISSING_REASON_LABEL: Record<string, string> = {
  not_in_phase1_universe: "不在試驗版管理範圍",
  phase1_snapshot_missing: "尚無試驗版快照",
  phase1_snapshot_stale: "試驗版快照已過期",
  phase1_snapshot_read_failed: "試驗版快照讀取失敗",
};

interface WatchlistTechnicalState {
  loading: boolean;
  expanded: boolean;
  error: string | null;
  result: AnalyzeResponse | null;
  copyStatus: CopyStatus;
}

interface BulkTechnicalLookupState {
  running: boolean;
  completed: number;
  total: number;
}

const EMPTY_TECHNICAL_STATE: WatchlistTechnicalState = {
  loading: false,
  expanded: false,
  error: null,
  result: null,
  copyStatus: "idle",
};

function formatPhase1Distance(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatPhase1MissingReason(reason: string | null | undefined): string {
  if (!reason) return "資料不足";
  return PHASE1_MISSING_REASON_LABEL[reason] ?? reason;
}

function getPhase1DisplayAnchors(observation: Phase1Observation): Array<{
  key: string;
  label: string;
  avwap?: number | null;
  distance?: number | null;
  anchorDate?: string | null;
  estimated?: boolean;
}> {
  const entries = Object.entries(observation.anchors ?? {}).filter(([, anchor]) => anchor.available !== false);
  const priority: Map<string, number> = new Map(PHASE1_ANCHOR_ORDER.map((key, index) => [key, index]));

  return entries
    .sort(([left], [right]) => (priority.get(left) ?? 99) - (priority.get(right) ?? 99) || left.localeCompare(right))
    .slice(0, 4)
    .map(([key, anchor]) => ({
      key,
      label: PHASE1_ANCHOR_LABEL[key] ?? key,
      avwap: anchor.avwap,
      distance: anchor.current_distance_to_avwap_pct ?? anchor.distance_to_avwap_pct,
      anchorDate: anchor.anchor_date,
      estimated: anchor.estimated,
    }));
}

function WatchlistPhase1Observation({
  observation,
  symbol,
}: {
  observation: Phase1Observation;
  symbol: string;
}) {
  const anchors = getPhase1DisplayAnchors(observation);
  const isMissing = observation.freshness === "missing" || Boolean(observation.missing_reason);

  return (
    <div className="mt-4 border-t border-border-subtle pt-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-text-primary">試驗版 AVWAP 觀察</p>
          <p className="mt-1 text-xs text-text-muted">
            {observation.data_date} · {observation.adjustment_mode}
          </p>
        </div>
        <span
          className={`rounded-md border px-2 py-0.5 text-xs font-medium ${
            isMissing
              ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
              : "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
          }`}
        >
          {isMissing ? formatPhase1MissingReason(observation.missing_reason) : "快照可用"}
        </span>
      </div>

      {anchors.length > 0 ? (
        <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          {anchors.map((anchor) => (
            <div key={anchor.key}>
              <p className="mb-1 text-xs text-text-muted">{anchor.label}</p>
              <p className="font-mono text-sm font-medium text-text-primary">
                {formatPrice(anchor.avwap, symbol)}
                <span
                  className={`ml-2 ${
                    anchor.distance == null
                      ? "text-text-faint"
                      : anchor.distance >= 0
                        ? "text-emerald-600 dark:text-emerald-300"
                        : "text-red-600 dark:text-red-300"
                  }`}
                >
                  {formatPhase1Distance(anchor.distance)}
                </span>
              </p>
              {anchor.anchorDate && <p className="mt-1 text-xs text-text-faint">{anchor.anchorDate}</p>}
              {anchor.estimated && <p className="mt-1 text-xs text-amber-600 dark:text-amber-300">日資料估算</p>}
            </div>
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-border-subtle px-3 py-2 text-xs text-text-muted">
          {formatPhase1MissingReason(observation.missing_reason)}
        </p>
      )}
    </div>
  );
}

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
  const phase1Observation = quickResult?.phase1_observation ?? null;
  const technicalProfile = quickResult?.technical_profile ?? null;
  const quickSessionLabel = quickResult?.is_final === false ? "盤中" : "收盤";

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

      {quickResult && !technicalState.error && (
        <TechnicalIndicatorsPanel
          result={quickResult}
          snapshot={quickSnapshot}
          compact
          className="mt-3"
          showProfileDisclosure={false}
        />
      )}

      {phase1Observation && (
        <WatchlistPhase1Observation
          observation={phase1Observation}
          symbol={quickSnapshotSymbol}
        />
      )}

      {technicalProfile && (
        <TechnicalProfileDisclosure
          profile={technicalProfile}
          responseIsFinal={quickResult?.is_final}
          className="mt-4"
        />
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
  const [bulkTechnicalLookup, setBulkTechnicalLookup] = useState<BulkTechnicalLookupState | null>(null);
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
  const technicalResultCount = useMemo(
    () => sortedItems.filter((item) => Boolean(technicalStateByItemId[item.id]?.result)).length,
    [sortedItems, technicalStateByItemId],
  );
  const bulkTechnicalLookupRunning = bulkTechnicalLookup?.running === true;
  const listActionBusy = reordering || busyItemId !== null || bulkTechnicalLookupRunning;
  const allItemsHaveTechnicalResults = itemCount > 0 && technicalResultCount === itemCount;
  const bulkLookupButtonLabel = bulkTechnicalLookupRunning
    ? `快查中 ${bulkTechnicalLookup?.completed ?? 0}/${bulkTechnicalLookup?.total ?? itemCount}`
    : allItemsHaveTechnicalResults
      ? "重新快查全部"
      : technicalResultCount > 0
        ? `補查 ${itemCount - technicalResultCount} 檔`
        : itemCount > 0
          ? `一鍵快查 ${itemCount} 檔`
          : "一鍵快查";

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

  async function loadTechnicalLookup(
    item: WatchlistItem,
    {
      forceRefresh = false,
      expandCached = false,
      toggleCached = false,
    }: {
      forceRefresh?: boolean;
      expandCached?: boolean;
      toggleCached?: boolean;
    } = {},
  ): Promise<void> {
    const currentState = getTechnicalState(item.id);
    if (currentState.loading) return;

    if (currentState.result && !forceRefresh) {
      updateTechnicalState(item.id, (state) => ({
        ...state,
        expanded: toggleCached ? !state.expanded : expandCached || state.expanded,
        error: null,
      }));
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

  async function handleQuickTechnicalLookup(item: WatchlistItem, forceRefresh = false) {
    await loadTechnicalLookup(item, { forceRefresh, toggleCached: !forceRefresh, expandCached: true });
  }

  async function handleBulkTechnicalLookup() {
    if (bulkTechnicalLookupRunning || loading || itemCount === 0 || reordering || busyItemId !== null) return;

    const shouldRefreshAll = allItemsHaveTechnicalResults;
    const cachedItems = shouldRefreshAll
      ? []
      : sortedItems.filter((item) => Boolean(getTechnicalState(item.id).result));
    cachedItems.forEach((item) => {
      updateTechnicalState(item.id, (state) => ({ ...state, expanded: true, error: null }));
    });

    const targetItems = shouldRefreshAll
      ? sortedItems
      : sortedItems.filter((item) => !getTechnicalState(item.id).result);
    if (targetItems.length === 0) return;

    setBulkTechnicalLookup({ running: true, completed: 0, total: targetItems.length });
    setError(null);

    let nextIndex = 0;
    const workerCount = Math.min(3, targetItems.length);
    const runWorker = async () => {
      while (nextIndex < targetItems.length) {
        const item = targetItems[nextIndex];
        nextIndex += 1;
        await loadTechnicalLookup(item, { forceRefresh: true, expandCached: true });
        setBulkTechnicalLookup((state) =>
          state ? { ...state, completed: Math.min(state.completed + 1, state.total) } : state,
        );
      }
    };

    try {
      await Promise.all(Array.from({ length: workerCount }, runWorker));
    } finally {
      setBulkTechnicalLookup(null);
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
    if (listActionBusy) {
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
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-text-primary">關注列表</h2>
            <p className="mt-1 text-xs text-text-muted">
              儲存還沒進入持股的觀察標的，可在列表內快速查看技術指標並複製摘要。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:justify-end">
            <span className="text-xs text-text-faint">{itemCount} 檔關注中</span>
            <button
              type="button"
              onClick={() => void handleBulkTechnicalLookup()}
              disabled={loading || itemCount === 0 || listActionBusy}
              className="rounded-lg border border-indigo-500/70 bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:border-border disabled:bg-card-hover disabled:text-text-faint"
            >
              {bulkLookupButtonLabel}
            </button>
          </div>
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
              const listBusy = listActionBusy;
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
                    rows={4}
                    placeholder="補充觀察條件"
                    disabled={itemBusy}
                    className="min-h-32 w-full resize-y rounded-lg border border-input-border bg-input-bg px-3 py-2 text-sm text-text-primary outline-none ring-indigo-200 transition focus:ring-2 disabled:opacity-60 dark:ring-indigo-500"
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
