export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30",
    done: "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30",
    failed: "bg-red-500/20 text-red-400 border border-red-500/30",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono ${styles[status] ?? "bg-slate-700 text-slate-400"}`}>
      {status === "running" && (
        <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
      )}
      {status}
    </span>
  );
}
