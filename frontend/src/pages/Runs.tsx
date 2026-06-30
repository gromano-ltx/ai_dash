import { useState } from "react";
import { useRuns, useUsers } from "../lib/api";
import { useActiveUser } from "../lib/UserContext";
import { fmt, duration } from "../lib/format";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";
import { useNavigate } from "react-router-dom";

function ticketUrl(ref: string): string | null {
  if (/^LINEAR-\d+$/i.test(ref)) return `https://linear.app/issue/${ref.toUpperCase()}`;
  return null;
}

export function Runs() {
  const [provider, setProvider] = useState("");
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [ticket, setTicket] = useState("");
  const { data: usersData } = useUsers();
  const { user: globalUser } = useActiveUser();
  const { data: runs, isLoading } = useRuns({
    provider: provider || undefined,
    status: status || undefined,
    user: user || globalUser || undefined,
    ticket: ticket || undefined,
  });
  const navigate = useNavigate();

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-lg font-mono font-semibold text-slate-100">Runs</h1>
        <p className="text-sm text-slate-500 mt-0.5">{runs?.length ?? 0} results</p>
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
            className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono focus:outline-none focus:border-slate-500"
          >
            {options.map((o) => (
              <option key={o} value={o}>{o || label}</option>
            ))}
          </select>
        ))}
        <select
          value={user}
          onChange={(e) => setUser(e.target.value)}
          className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono focus:outline-none focus:border-slate-500"
        >
          <option value="">User</option>
          {usersData?.users.map((u) => (
            <option key={u} value={u}>{u}</option>
          ))}
        </select>
        <input
          placeholder="Ticket (e.g. LINEAR-123)"
          value={ticket}
          onChange={(e) => setTicket(e.target.value)}
          className="bg-slate-900 border border-slate-700 text-slate-300 text-sm rounded px-3 py-1.5 font-mono focus:outline-none focus:border-slate-500 w-48"
        />
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800">
              {["Task", "Provider", "Model", "User", "Status", "Duration", "Tokens", "Ticket", "Commits", "PRs"].map((h) => (
                <th key={h} className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={10} className="px-4 py-8 text-center text-slate-600 font-mono text-sm">loading…</td></tr>
            )}
            {runs?.map((run) => (
              <tr
                key={run.id}
                onClick={() => navigate(`/runs/${run.id}`)}
                className="border-b border-slate-800/50 hover:bg-slate-800/40 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 max-w-xs">
                  <p className="text-slate-200 truncate">{run.label}</p>
                </td>
                <td className="px-4 py-3"><ProviderBadge provider={run.provider} /></td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs whitespace-nowrap">{run.model}</td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs">{run.user ?? "—"}</td>
                <td className="px-4 py-3"><StatusBadge status={run.status} /></td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs whitespace-nowrap">{duration(run.duration_seconds)}</td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs whitespace-nowrap">{fmt(run.input_tokens + run.output_tokens)}</td>
                <td className="px-4 py-3 text-xs font-mono">
                  {run.ticket_refs[0]
                    ? (() => {
                        const url = ticketUrl(run.ticket_refs[0]);
                        return url
                          ? <a href={url} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-violet-400 hover:underline">{run.ticket_refs[0]}</a>
                          : <span className="text-violet-400">{run.ticket_refs[0]}</span>;
                      })()
                    : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-3 text-slate-400 font-mono text-xs">{run.git_commits.length || "—"}</td>
                <td className="px-4 py-3 text-xs font-mono">
                  {run.git_prs.length > 0
                    ? <a href={run.git_prs[0]} target="_blank" rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-blue-400 hover:underline">
                        PR #{run.git_prs[0].match(/\/pull\/(\d+)/)?.[1] ?? "↗"}
                        {run.git_prs.length > 1 && <span className="text-slate-500 ml-1">+{run.git_prs.length - 1}</span>}
                      </a>
                    : <span className="text-slate-600">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
