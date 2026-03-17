// frontend/src/components/ConfidenceChart.tsx
interface DataPoint {
  date: string;
  confidence: number | null;
  actionTag: string | null;
  prevActionTag: string | null;
  isFinal: boolean;
}

const ACTION_COLOR: Record<string, string> = {
  Hold: "#22c55e", // green-500
  Trim: "#eab308", // yellow-500
  Exit: "#ef4444", // red-500
  Add: "#3b82f6",  // blue-500
};

const ACTION_LABEL: Record<string, string> = {
  Hold: "續抱",
  Trim: "減碼",
  Exit: "出場",
  Add: "加碼",
};

interface Props {
  data: DataPoint[];
  height?: number;
}

export function ConfidenceChart({ data, height = 200 }: Props) {
  const validPoints = data.filter((d) => d.confidence !== null);
  if (validPoints.length === 0) {
    return (
      <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">
        無歷史數據
      </div>
    );
  }

  const W = 600;
  const H = height;
  const PAD = { top: 16, right: 16, bottom: 32, left: 40 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const minY = 0;
  const maxY = 100;
  const xStep = innerW / Math.max(validPoints.length - 1, 1);

  const toX = (i: number) => PAD.left + i * xStep;
  const toY = (v: number) => PAD.top + innerH - ((v - minY) / (maxY - minY)) * innerH;

  // 折線 path
  const linePath = validPoints
    .map((d, i) => `${i === 0 ? "M" : "L"} ${toX(i)} ${toY(d.confidence!)}`)
    .join(" ");

  // Y 軸格線
  const gridLines = [25, 50, 75, 100];

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 320 }}>
        {/* 格線 */}
        {gridLines.map((y) => (
          <g key={y}>
            <line
              x1={PAD.left}
              y1={toY(y)}
              x2={W - PAD.right}
              y2={toY(y)}
              stroke="var(--color-border)"
              strokeWidth={1}
            />
            <text x={PAD.left - 6} y={toY(y) + 4} textAnchor="end" fontSize={10} fill="var(--color-text-faint)">
              {y}
            </text>
          </g>
        ))}

        {/* 折線 */}
        <path d={linePath} fill="none" stroke="#6366f1" strokeWidth={2} />

        {/* 各點標記 */}
        {validPoints.map((d, i) => {
          const x = toX(i);
          const y = toY(d.confidence!);
          const color = ACTION_COLOR[d.actionTag ?? ""] ?? "#94a3b8";
          const isSignalChange = d.prevActionTag && d.prevActionTag !== d.actionTag;
          const isIntraday = !d.isFinal;

          return (
            <g key={i}>
              {/* 訊號轉向時顯示垂直虛線 */}
              {isSignalChange && (
                <line
                  x1={x}
                  y1={PAD.top}
                  x2={x}
                  y2={H - PAD.bottom}
                  stroke={color}
                  strokeWidth={1}
                  strokeDasharray="4 2"
                  opacity={0.5}
                />
              )}
              {/* 資料點圓圈 */}
              <circle
                cx={x}
                cy={y}
                r={isSignalChange ? 6 : 4}
                fill={color}
                stroke="white"
                strokeWidth={1.5}
              />
              {isIntraday && (
                <circle
                  cx={x}
                  cy={y}
                  r={8}
                  fill="none"
                  stroke="#f59e0b"
                  strokeWidth={1.5}
                  strokeDasharray="3 2"
                />
              )}
              {/* 日期標籤（每隔 5 點顯示一個） */}
              {i % 5 === 0 && (
                <text x={x} y={H - PAD.bottom + 14} textAnchor="middle" fontSize={9} fill="var(--color-text-faint)">
                  {d.date.slice(5)} {/* MM-DD */}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* 圖例 */}
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-text-secondary">
        {Object.entries(ACTION_LABEL).map(([tag, label]) => (
          <span key={tag} className="flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: ACTION_COLOR[tag] }}
            />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1 text-text-muted">
          <span className="inline-block h-px w-4 border-t border-dashed border-slate-400" />
          訊號轉向
        </span>
        <span className="flex items-center gap-1 text-amber-600">
          <span className="inline-block h-2.5 w-2.5 rounded-full border border-dashed border-amber-500" />
          盤中未定稿
        </span>
      </div>
    </div>
  );
}
