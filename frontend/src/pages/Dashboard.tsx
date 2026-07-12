import { useState } from "react";
import { useStats, useDaily } from "../lib/api";
import { fmt } from "../lib/format";
import { Odometer } from "../components/Odometer";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";

const PROVIDER_COLORS: Record<string, string> = {
  anthropic: "#b8935a",
  openai:    "#6b8f7a",
  gemini:    "#7288a3",
};

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai:    "OpenAI",
  gemini:    "Gemini",
};

const TOOLTIP_STYLE = {
  backgroundColor: "#1e1b16",
  border: "1px solid #3a352c",
  borderRadius: "0px",
  color: "#e8e2d4",
  fontSize: "12px",
  fontFamily: "IBM Plex Mono, monospace",
};

const AXIS_TICK = { fill: "#6b6355", fontSize: 11, fontFamily: "IBM Plex Mono, monospace" };

function StatCard({
  label, value, sub, accent, odometer,
}: {
  label: string; value: string | number; sub?: string; accent: string; odometer?: boolean;
}) {
  return (
    <div className="bg-ledger-surface border-t-2 p-5 flex flex-col" style={{ borderTopColor: accent }}>
      <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider mb-2">{label}</p>
      <p className="text-2xl font-mono font-medium text-ledger-ink">
        {odometer && typeof value === "string" ? <Odometer value={value} /> : value}
      </p>
      {sub && <p className="text-xs font-mono text-ledger-faint mt-1">{sub}</p>}
    </div>
  );
}

function ProviderRow({ name, runs, tokens, commits }: {
  name: string; runs: number; tokens: number; commits: number;
}) {
  const color = PROVIDER_COLORS[name] ?? "#6b6355";
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-ledger-rule last:border-b-0 text-sm">
      <div className="flex items-center gap-2.5">
        <span className="w-1.5 h-1.5" style={{ backgroundColor: color }} />
        <span className="font-sans text-ledger-ink">{PROVIDER_LABELS[name] ?? name}</span>
      </div>
      <div className="flex gap-6 font-mono text-ledger-dim tabular-nums">
        <span className="w-20 text-right whitespace-nowrap">{runs} run{runs !== 1 ? "s" : ""}</span>
        <span className="w-24 text-right whitespace-nowrap">{fmt(tokens)} tok</span>
        <span className="w-28 text-right whitespace-nowrap">{commits} commit{commits !== 1 ? "s" : ""}</span>
      </div>
    </div>
  );
}

const TIME_RANGES = [
  { label: "24h", days: 1 },
  { label: "7d",  days: 7 },
  { label: "30d", days: 30 },
  { label: "90d", days: 90 },
  { label: "All", days: 365 },
];

export function Dashboard() {
  const [days, setDays] = useState(7);
  const { data: stats } = useStats(undefined, days);
  const { data: daily } = useDaily(undefined, days);

  const totalTokens = stats
    ? stats.total_input_tokens_7d + stats.total_output_tokens_7d
    : 0;

  const byProvider = stats?.by_provider ?? {};

  const tokenData = (daily ?? []).map((d) => ({
    date: d.date,
    input: d.input_tokens,
    output: d.output_tokens,
  }));

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-y-2">
        <div>
          <h1 className="text-lg font-sans font-semibold text-ledger-ink">Overview</h1>
          <p className="text-sm font-mono text-ledger-faint mt-0.5">Last {days === 1 ? "24 hours" : `${days} days`}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex border border-ledger-rule">
            {TIME_RANGES.map(({ label, days: d }) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-3 py-1 text-xs font-mono transition-colors ${
                  days === d
                    ? "bg-ledger-raised text-ledger-ink"
                    : "text-ledger-faint hover:text-ledger-dim hover:bg-ledger-raised/50"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {stats?.running_count ? (
            <div className="flex items-center gap-2 text-sm font-mono text-ledger-amber">
              <span className="w-2 h-2 rounded-full bg-ledger-amber animate-pulse" />
              {stats.running_count} active
            </div>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Runs" value={stats?.total_runs_7d ?? "—"} accent="#b8935a" />
        <StatCard
          label="Tokens"
          value={stats ? fmt(totalTokens) : "—"}
          sub={stats ? `${fmt(stats.total_input_tokens_7d)} in · ${fmt(stats.total_output_tokens_7d)} out` : undefined}
          accent="#c17f2e"
          odometer
        />
        <StatCard label="Commits" value={stats?.total_commits_7d ?? "—"} accent="#6b8f7a" />
        <StatCard label="PRs Opened" value={stats?.total_prs_7d ?? "—"} accent="#7288a3" />
        <StatCard
          label="Est. Spend"
          value={stats ? `$${stats.total_cost_usd.toFixed(2)}` : "—"}
          sub="estimated; pricing may change"
          accent="#a8320c"
        />
        <StatCard
          label="PR Merge Success Rate"
          value={
            stats && stats.pr_merge_success_rate !== null
              ? `${Math.round(stats.pr_merge_success_rate * 100)}%`
              : "—"
          }
          sub={
            stats && stats.pr_merge_success_rate !== null
              ? `${stats.pr_merge_success_merged}/${stats.pr_merge_success_resolved} merged`
              : "no resolved PRs in range"
          }
          accent="#5a8f6b"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-ledger-surface border border-ledger-rule p-5">
          <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider mb-4">Runs per day</p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={daily ?? []} barSize={16} barGap={2} barCategoryGap="30%">
              <CartesianGrid strokeDasharray="2 3" stroke="#3a352c" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} />
              <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} allowDecimals={false} width={20} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#3a352c" }} />
              <Bar dataKey="anthropic" stackId="a" fill={PROVIDER_COLORS.anthropic} />
              <Bar dataKey="openai"    stackId="a" fill={PROVIDER_COLORS.openai} />
              <Bar dataKey="gemini"    stackId="a" fill={PROVIDER_COLORS.gemini} />
            </BarChart>
          </ResponsiveContainer>
          <div className="flex gap-4 mt-2 justify-end">
            {["anthropic", "openai", "gemini"].map((p) => (
              <span key={p} className="flex items-center gap-1.5 text-xs font-mono text-ledger-faint">
                <span className="w-2 h-2" style={{ backgroundColor: PROVIDER_COLORS[p] }} />
                {p}
              </span>
            ))}
          </div>
        </div>

        <div className="bg-ledger-surface border border-ledger-rule p-5">
          <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider mb-4">Token burn / day</p>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={tokenData} barSize={16} barCategoryGap="30%">
              <CartesianGrid strokeDasharray="2 3" stroke="#3a352c" vertical={false} />
              <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} />
              <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={36}
                tickFormatter={(v) => fmt(v)} />
              <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#3a352c" }}
                formatter={(v, name) => [fmt(Number(v ?? 0)), String(name)]} />
              <Bar dataKey="input"  stackId="t" fill="#a8320c" />
              <Bar dataKey="output" stackId="t" fill="#c17f2e" />
            </BarChart>
          </ResponsiveContainer>
          <div className="flex gap-4 mt-2 justify-end">
            <span className="flex items-center gap-1.5 text-xs font-mono text-ledger-faint">
              <span className="w-2 h-2 bg-ledger-accent" />input
            </span>
            <span className="flex items-center gap-1.5 text-xs font-mono text-ledger-faint">
              <span className="w-2 h-2 bg-ledger-amber" />output
            </span>
          </div>
        </div>
      </div>

      <div className="bg-ledger-surface border border-ledger-rule p-5">
        <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider mb-1">Provider breakdown</p>
        {["anthropic", "openai", "gemini"].map((p) => {
          const d = byProvider[p] ?? { runs: 0, input_tokens: 0, output_tokens: 0, commits: 0 };
          return (
            <ProviderRow
              key={p}
              name={p}
              runs={d.runs}
              tokens={d.input_tokens + d.output_tokens}
              commits={d.commits}
            />
          );
        })}
      </div>
    </div>
  );
}
