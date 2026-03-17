export function InsightText({
  text,
  emptyText = "請先執行分析。",
}: {
  text: string | null | undefined;
  emptyText?: string;
}) {
  if (!text) return <p className="text-sm text-text-faint">{emptyText}</p>;
  const sentences = text.split(/(?<=[。；！？：\n])/).map((s) => s.trim()).filter(Boolean);
  if (sentences.length <= 1)
    return <p className="text-sm leading-relaxed text-text-secondary">{text}</p>;
  return (
    <div className="space-y-1.5">
      {sentences.map((s, i) => (
        <p key={i} className="text-sm leading-relaxed text-text-secondary">{s}</p>
      ))}
    </div>
  );
}
