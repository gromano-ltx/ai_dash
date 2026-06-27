import { useParams, useNavigate } from "react-router-dom";
import { useRun } from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";

function fmt(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function duration(secs: number | null) {
  if (!secs) return "—";
  if (secs < 60) return `${Math.round(secs)}s`;
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
}

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(id!);

  if (isLoading) return <div className="p-6 text-slate-500 font-mono text-sm">loading…</div>;
  if (!run) return <div className="p-6 text-slate-500 font-mono text-sm">run not found</div>;

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <button
        onClick={() => navigate(-1)}
        className="text-xs text-slate-500 hover:text-slate-300 font-mono transition-colors"
      >
        ← back
      </button>

      <div className="space-y-2">
        <h1 className="text-lg font-mono font-semibold text-slate-100">{run.label}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <ProviderBadge provider={run.provider} />
          <StatusBadge status={run.status} />
          <span className="text-xs font-mono text-slate-500">{run.model}</span>
          {run.user && <span className="text-xs font-mono text-slate-500">by {run.user}</span>}
          <span className="text-xs font-mono text-slate-500">{duration(run.duration_seconds)}</span>
        </div>
        {run.ticket_refs.length > 0 && (
          <div className="flex gap-1.5 flex-wrap">
            {run.ticket_refs.map((t) => (
              <span key={t} className="px-2 py-0.5 rounded text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      {run.task_description && (
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
          <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-2">Task</p>
          <p className="text-sm text-slate-300">{run.task_description}</p>
        </div>
      )}

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
        <p className="text-xs text-slate-500 font-mono uppercase tracking-wider">Activity Timeline</p>
        <div className="space-y-2 text-sm">
          <TimelineItem icon="◎" label="Task started" detail={new Date(run.started_at).toLocaleString()} />
          {run.git_commits.map((hash) => (
            <TimelineItem key={hash} icon="⬡" label="Commit" detail={hash} mono />
          ))}
          {run.git_prs.map((url) => (
            <TimelineItem key={url} icon="↗" label="PR opened" detail={url} link={url} />
          ))}
          {run.ended_at && (
            <TimelineItem
              icon={run.status === "done" ? "✓" : "✗"}
              label={run.status}
              detail={new Date(run.ended_at).toLocaleString()}
            />
          )}
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
        <p className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-3">Tokens</p>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <p className="text-xl font-mono font-semibold text-slate-100">{fmt(run.input_tokens)}</p>
            <p className="text-xs text-slate-500 mt-0.5">input</p>
          </div>
          <div>
            <p className="text-xl font-mono font-semibold text-slate-100">{fmt(run.output_tokens)}</p>
            <p className="text-xs text-slate-500 mt-0.5">output</p>
          </div>
          <div>
            <p className="text-xl font-mono font-semibold text-slate-100">{fmt(run.input_tokens + run.output_tokens)}</p>
            <p className="text-xs text-slate-500 mt-0.5">total</p>
          </div>
        </div>
        <div className="mt-3 flex rounded overflow-hidden h-2">
          <div
            className="bg-violet-500"
            style={{ width: `${(run.input_tokens / (run.input_tokens + run.output_tokens)) * 100}%` }}
          />
          <div className="flex-1 bg-emerald-500" />
        </div>
        <div className="flex justify-between text-xs text-slate-500 font-mono mt-1">
          <span>input</span><span>output</span>
        </div>
      </div>
    </div>
  );
}

function TimelineItem({ icon, label, detail, mono, link }: {
  icon: string; label: string; detail: string; mono?: boolean; link?: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-slate-500 w-4 shrink-0 mt-0.5">{icon}</span>
      <span className="text-slate-400 w-24 shrink-0">{label}</span>
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline truncate text-xs font-mono">
          {detail}
        </a>
      ) : (
        <span className={`text-slate-300 truncate ${mono ? "font-mono text-xs" : ""}`}>{detail}</span>
      )}
    </div>
  );
}
