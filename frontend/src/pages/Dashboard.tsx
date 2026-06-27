import { useStats, useDaily } from "../lib/api";
import {
  BarChart, Bar, AreaChart, Area,
  XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from "recharts";

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

const PROVIDER_COLORS = {
  anthropic: "#8b5cf6",
  openai: "#22c55e",
  gemini: "#3b82f6",
};

const tooltipStyle = {
  backgroundColor: "#0f172a",
  border: "1px solid #1e293b",
  borderRadius: "6px",
  color: "#cbd5e1",
  fontSize: "12px",
  fontFamily: "monospace",
};

export function Dashboard() {
  const { data: stats } = useStats();
  const { data: daily } = useDaily();

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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-4">Runs per day</p>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={daily ?? []} barSize={14} barGap={2}>
              <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 11, fontFamily: "monospace" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "#64748b", fontSize: 11, fontFamily: "monospace" }} axisLine={false} tickLine={false} allowDecimals={false} width={24} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ fill: "#1e293b" }} />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: "monospace", color: "#94a3b8", paddingTop: 8 }} />
              <Bar dataKey="anthropic" stackId="a" fill={PROVIDER_COLORS.anthropic} radius={[0, 0, 0, 0]} />
              <Bar dataKey="openai" stackId="a" fill={PROVIDER_COLORS.openai} radius={[0, 0, 0, 0]} />
              <Bar dataKey="gemini" stackId="a" fill={PROVIDER_COLORS.gemini} radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-4">Token burn</p>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={daily ?? []}>
              <defs>
                <linearGradient id="inputGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="outputGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 11, fontFamily: "monospace" }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "#64748b", fontSize: 11, fontFamily: "monospace" }} axisLine={false} tickLine={false} width={40}
                tickFormatter={(v) => fmt(v)} />
              <Tooltip contentStyle={tooltipStyle} cursor={{ stroke: "#334155" }}
                formatter={(v: number) => [fmt(v), undefined]} />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: "monospace", color: "#94a3b8", paddingTop: 8 }} />
              <Area type="monotone" dataKey="input_tokens" name="input" stroke="#8b5cf6" strokeWidth={2} fill="url(#inputGrad)" dot={false} />
              <Area type="monotone" dataKey="output_tokens" name="output" stroke="#22c55e" strokeWidth={2} fill="url(#outputGrad)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
