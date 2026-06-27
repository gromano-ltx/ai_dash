import { useStats, useRuns } from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";
import { useNavigate } from "react-router-dom";

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
      <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-1">{label}</p>
      <p className="text-2xl font-mono font-semibold text-slate-100">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function Dashboard() {
  const { data: stats } = useStats();
  const { data: runs } = useRuns();
  const navigate = useNavigate();
  const recent = runs?.slice(0, 8) ?? [];

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-lg font-mono font-semibold text-slate-100">Overview</h1>
        <p className="text-sm text-slate-500 mt-0.5">Last 7 days</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Total Runs" value={stats?.total_runs_7d ?? "—"} />
        <StatCard
          label="Tokens"
          value={stats ? fmt(stats.total_input_tokens_7d + stats.total_output_tokens_7d) : "—"}
          sub={stats ? `${fmt(stats.total_input_tokens_7d)} in · ${fmt(stats.total_output_tokens_7d)} out` : undefined}
        />
        <StatCard label="Commits" value={stats?.total_commits_7d ?? "—"} />
        <StatCard label="PRs Opened" value={stats?.total_prs_7d ?? "—"} />
      </div>

      {stats?.running_count ? (
        <div className="flex items-center gap-2 text-sm text-yellow-400">
          <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
          {stats.running_count} run{stats.running_count > 1 ? "s" : ""} currently active
        </div>
      ) : null}

      <div>
        <h2 className="text-sm font-mono text-slate-400 mb-3 uppercase tracking-wider">Recent Runs</h2>
        <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800">
                <th className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal">Task</th>
                <th className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal">Provider</th>
                <th className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal">User</th>
                <th className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal">Status</th>
                <th className="text-left px-4 py-2.5 text-xs font-mono text-slate-500 font-normal">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((run) => (
                <tr
                  key={run.id}
                  onClick={() => navigate(`/runs/${run.id}`)}
                  className="border-b border-slate-800/50 hover:bg-slate-800/40 cursor-pointer transition-colors"
                >
                  <td className="px-4 py-3">
                    <p className="text-slate-200 truncate max-w-xs">{run.label}</p>
                    {run.ticket_refs.length > 0 && (
                      <span className="text-xs text-slate-500 font-mono">{run.ticket_refs[0]}</span>
                    )}
                  </td>
                  <td className="px-4 py-3"><ProviderBadge provider={run.provider} /></td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">{run.user ?? "—"}</td>
                  <td className="px-4 py-3"><StatusBadge status={run.status} /></td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">
                    {fmt(run.input_tokens + run.output_tokens)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
