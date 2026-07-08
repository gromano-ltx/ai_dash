import { useEffect, useState } from "react";
import { useRuns, useUsers, useMe } from "../lib/api";
import { fmt, duration } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";
import { useNavigate } from "react-router-dom";
import { ticketUrl, prLabel, commitUrl, repoBase } from "../lib/links";

const PAGE_SIZE = 50;

export function Runs() {
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [ticket, setTicket] = useState("");
  const [page, setPage] = useState(0);
  const { data: me } = useMe();
  const { data: usersData } = useUsers();
  const { data: runs, isLoading } = useRuns({
    provider: provider || undefined,
    status: status || undefined,
    user: user || undefined,
    ticket: ticket || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });
  const navigate = useNavigate();

  // Reset to the first page whenever any filter changes.
  useEffect(() => {
    setPage(0);
  }, [provider, status, user, ticket]);

  // No total-count endpoint exists, so infer "more pages" from a full page
  // coming back — a short page means this was the last one.
  const hasNextPage = (runs?.length ?? 0) >= PAGE_SIZE;

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-lg font-sans font-semibold text-ledger-ink">Runs</h1>
        <p className="text-sm font-mono text-ledger-faint mt-0.5">
          Showing {runs?.length ?? 0} results (page {page + 1})
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {[
          { label: "Provider", value: provider, set: setProvider, options: ["", "anthropic", "openai", "gemini"] },
          { label: "Status", value: status, set: setStatus, options: ["", "running", "done", "failed"] },
        ].map(({ label, value, set, options }) => (
          <select
            key={label}
            value={value}
            onChange={(e) => set(e.target.value)}
            className="bg-ledger-surface border border-ledger-rule text-ledger-dim text-sm px-3 py-1.5 font-mono focus:outline-none focus:border-ledger-faint"
          >
            {options.map((o) => (
              <option key={o} value={o}>{o || label}</option>
            ))}
          </select>
        ))}
        {me?.is_admin && (
          <select
            value={user}
            onChange={(e) => setUser(e.target.value)}
            className="bg-ledger-surface border border-ledger-rule text-ledger-dim text-sm px-3 py-1.5 font-mono focus:outline-none focus:border-ledger-faint"
          >
            <option value="">User</option>
            {usersData?.users.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        )}
        <input
          placeholder="Ticket (e.g. LINEAR-123)"
          value={ticket}
          onChange={(e) => setTicket(e.target.value)}
          className="bg-ledger-surface border border-ledger-rule text-ledger-dim text-sm px-3 py-1.5 font-mono focus:outline-none focus:border-ledger-faint w-48"
        />
      </div>

      <div className="bg-ledger-surface border border-ledger-rule overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-ledger-rule">
              {["Task", "Provider", "Model", "User", "Status", "Duration", "Tokens", "Ticket", "Code"].map((h) => (
                <th key={h} className="text-left px-4 py-2.5 text-xs font-sans text-ledger-faint uppercase tracking-wider font-normal whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="px-4 py-8 text-center text-ledger-faint font-mono text-sm">loading…</td></tr>
            )}
            {runs?.map((run) => (
              <tr
                key={run.id}
                onClick={() => navigate(`/runs/${run.id}`)}
                className="border-b border-ledger-rule/50 hover:bg-ledger-raised/40 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 max-w-xs">
                  <p className="text-ledger-ink font-sans truncate">{run.label}</p>
                </td>
                <td className="px-4 py-3"><ProviderBadge provider={run.provider} /></td>
                <td className="px-4 py-3 text-ledger-dim font-mono text-xs whitespace-nowrap">{run.model}</td>
                <td className="px-4 py-3 text-ledger-dim font-mono text-xs">{run.user ?? "—"}</td>
                <td className="px-4 py-3"><StatusBadge status={run.status} /></td>
                <td className="px-4 py-3 text-ledger-dim font-mono text-xs whitespace-nowrap tabular-nums">{duration(run.duration_seconds)}</td>
                <td className="px-4 py-3 text-ledger-dim font-mono text-xs whitespace-nowrap text-right tabular-nums">{fmt(run.input_tokens + run.output_tokens)}</td>
                <td className="px-4 py-3 text-xs font-mono">
                  {run.ticket_refs.length > 1
                    ? <span className="text-ledger-dim">{run.ticket_refs.length} tickets</span>
                    : run.ticket_refs[0]
                    ? (() => {
                        const url = ticketUrl(run.ticket_refs[0]);
                        return url
                          ? <a href={url} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-ledger-accent hover:underline">{run.ticket_refs[0]}</a>
                          : <span className="text-ledger-faint">{run.ticket_refs[0]}</span>;
                      })()
                    : <span className="text-ledger-faint">—</span>}
                </td>
                <td className="px-4 py-3 text-xs font-mono">
                  {run.git_prs.length > 1
                    ? (() => {
                        const base = repoBase(run.git_prs[0]);
                        const label = `${run.git_prs.length} PRs · ${run.git_commits.length} commits`;
                        return base
                          ? <a href={`${base}/pulls`} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-provider-gemini hover:underline">{label}</a>
                          : <span className="text-ledger-dim">{label}</span>;
                      })()
                    : run.git_prs.length > 0
                    ? <a href={run.git_prs[0]} target="_blank" rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-provider-gemini hover:underline">
                        {prLabel(run.git_prs[0])}
                        {run.git_commits.length > 1 && <span className="text-ledger-faint ml-1">({run.git_commits.length} commits)</span>}
                      </a>
                    : run.git_commits.length > 0
                    ? (() => {
                        const hash = run.git_commits[0];
                        const url = commitUrl(hash, run.meta?.github_repo, run.git_prs);
                        const label = hash.slice(0, 7);
                        const extra = run.git_commits.length > 1 && <span className="text-ledger-faint ml-1">+{run.git_commits.length - 1}</span>;
                        return url
                          ? <a href={url} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-provider-gemini hover:underline">{label}{extra}</a>
                          : <span className="text-ledger-dim">{label}{extra}</span>;
                      })()
                    : <span className="text-ledger-faint">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0}
          className="bg-ledger-surface border border-ledger-rule text-ledger-dim text-sm px-3 py-1.5 font-mono disabled:opacity-40 disabled:cursor-not-allowed hover:enabled:border-ledger-faint"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => setPage((p) => p + 1)}
          disabled={!hasNextPage}
          className="bg-ledger-surface border border-ledger-rule text-ledger-dim text-sm px-3 py-1.5 font-mono disabled:opacity-40 disabled:cursor-not-allowed hover:enabled:border-ledger-faint"
        >
          Next
        </button>
      </div>
    </div>
  );
}
