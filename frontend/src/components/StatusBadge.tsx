export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    running: "text-ledger-amber",
    done: "text-ledger-dim",
    failed: "text-ledger-accent",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-mono uppercase tracking-wider ${styles[status] ?? "text-ledger-faint"}`}>
      {status === "running" && (
        <span className="w-1.5 h-1.5 rounded-full bg-ledger-amber animate-pulse" />
      )}
      {status}
    </span>
  );
}
