import { useStats, useDaily } from "../lib/api";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "#8b5cf6",
  openai:    "#22c55e",
  gemini:    "#3b82f6",
};

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai:    "OpenAI",
  gemini:    "Gemini",
};

const TOOLTIP_STYLE = {
  backgroundColor: "#0f172a",
  border: "1px solid #1e293b",
  borderRadius: "6px",
  color: "#cbd5e1",
  fontSize: "12px",
  fontFamily: "monospace",
};

const AXIS_TICK = { fill: "#475569", fontSize: 11, fontFamily: "monospace" };

function StatCard({
  label, value, sub, accent,
}: {
  label: string; value: string | number; sub?: string; accent: string;
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 flex gap-4 items-start">
      <div className={`w-1 self-stretch rounded-full`} style={{ backgroundColor: accent }} />
      <div>
        <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-1">{label}</p>
        <p className="text-2xl font-mono font-semibold text-slate-100">{value}</p>
        {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
      </div>
    </div>
  );
}

function ProviderRow({ name, runs, tokens, commits, maxRuns }: {
  name: string; runs: number; tokens: number; commits: number; maxRuns: number;
}) {
  const pct = maxRuns > 0 ? (runs / maxRuns) * 100 : 0;
  const color = PROVIDER_COLORS[name] ?? "#64748b";
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between items-center text-xs font-mono">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
          <span className="text-slate-300">{PROVIDER_LABELS[name] ?? name}</span>
        </div>
        <div className="flex gap-4 text-slate-500">
          <span>{runs} run{runs !== 1 ? "s" : ""}</span>
          <span>{fmt(tokens)} tokens</span>
          <span>{commits} commits</span>
        </div>
      </div>
      <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
    </div>
  );
}

export function Dashboard() {
  const { data: stats } = useStats();
  const { data: daily } = useDaily();

  const totalTokens = stats
    ? stats.total_input_tokens_7d + stats.total_output_tokens_7d
    : 0;

  const byProvider = stats?.by_provider ?? {};
  const maxProviderRuns = Math.max(
    ...Object.values(byProvider).map((p) => p.runs), 1
  );

  const tokenData = (daily ?? []).map((d) => ({
    date: d.date,
    input: d.input_tokens,
    output: d.output_tokens,
  }));

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-lg font-mono font-semibold text-slate-100">Overview</h1>
          <p className="text-sm text-slate-500 mt-0.5">Last 7 days</p>
        </div>
        {stats?.running_count ? (
          <div className="flex items-center gap-2 text-sm text-yellow-400 font-mono">
            <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
            {stats.running_count} active
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Runs" value={stats?.total_runs_7d ?? "—"} accent="#8b5cf6" />
        <StatCard
          label="Tokens"
          value={stats ? fmt(totalTokens) : "—"}
          sub={stats ? `${fmt(stats.total_input_tokens_7d)} in · ${fmt(stats.total_output_tokens_7d)} out` : undefined}
          accent="#f59e0b"
        />
        <StatCard label="Commits" value={stats?.total_commits_7d ?? "—"} accent="#22c55e" />
        <StatCard label="PRs Opened" value={stats?.total_prs_7d ?? "—"} accent="#3b82f6" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-4">Runs per day</p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={daily ?? []} barSize={16} barGap={2} barCategoryGap="30%">
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} />
              <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} width={20} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#1e293b" }} />
              <Bar dataKey="anthropic" stackId="a" fill={PROVIDER_COLORS.anthropic} />
              <Bar dataKey="openai"    stackId="a" fill={PROVIDER_COLORS.openai} />
              <Bar dataKey="gemini"    stackId="a" fill={PROVIDER_COLORS.gemini} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="flex gap-4 mt-2 justify-end">
            {["anthropic", "openai", "gemini"].map((p) => (
              <span key={p} className="flex items-center gap-1.5 text-xs font-mono text-slate-500">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: PROVIDER_COLORS[p] }} />
                {p}
              </span>
            ))}
          </div>
        </div>

        <div className="bg-slate-900 border border-slate-800 rounded-lg p-5">
          <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-4">Token burn / day</p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={tokenData} barSize={16} barCategoryGap="30%">
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} />
              <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={36}
                tickFormatter={(v) => fmt(v)} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#1e293b" }}
                formatter={(v: number, name: string) => [fmt(v), name]} />
              <Bar dataKey="input"  stackId="t" fill="#8b5cf6" />
              <Bar dataKey="output" stackId="t" fill="#22c55e" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="flex gap-4 mt-2 justify-end">
            <span className="flex items-center gap-1.5 text-xs font-mono text-slate-500">
              <span className="w-2 h-2 rounded-full bg-violet-500" />input
            </span>
            <span className="flex items-center gap-1.5 text-xs font-mono text-slate-500">
              <span className="w-2 h-2 rounded-full bg-green-500" />output
            </span>
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
        <p className="text-xs text-slate-500 font-mono uppercase tracking-wider">Provider breakdown</p>
        {["anthropic", "openai", "gemini"].map((p) => {
          const d = byProvider[p] ?? { runs: 0, input_tokens: 0, output_tokens: 0, commits: 0 };
          return (
            <ProviderRow
              key={p}
              name={p}
              runs={d.runs}
              tokens={d.input_tokens + d.output_tokens}
              commits={d.commits}
              maxRuns={maxProviderRuns}
            />
          );
        })}
      </div>
    </div>
  );
}
