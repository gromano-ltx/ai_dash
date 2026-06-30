import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useRun, useRunChildren } from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";
import { fmt, duration } from "../lib/format";

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(id!);
  const { data: children } = useRunChildren(id!);
  const [subAgentsOpen, setSubAgentsOpen] = useState(true);

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
            {run.ticket_refs.map((t) => {
              const url = ticketUrl(t);
              return url ? (
                <a key={t} href={url} target="_blank" rel="noopener noreferrer"
                  className="px-2 py-0.5 rounded text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25 hover:bg-violet-500/25 transition-colors">
                  {t}
                </a>
              ) : (
                <span key={t} className="px-2 py-0.5 rounded text-xs font-mono bg-violet-500/15 text-violet-300 border border-violet-500/25">
                  {t}
                </span>
              );
            })}
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
          {run.git_commits.map((hash) => {
            const base = run.git_prs.length ? repoBase(run.git_prs[0]) : null;
            const url = base ? `${base}/commit/${hash}` : null;
            return <TimelineItem key={hash} icon="⬡" label="Commit" detail={hash} mono link={url ?? undefined} />;
          })}
          {run.git_prs.map((url) => (
            <TimelineItem key={url} icon="↗" label="PR opened" detail={prLabel(url)} link={url} />
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

      {children && children.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 space-y-3">
          <button
            onClick={() => setSubAgentsOpen((o) => !o)}
            className="flex items-center gap-2 w-full text-left"
          >
            <p className="text-xs text-slate-500 font-mono uppercase tracking-wider">
              Sub-agents ({children.length})
            </p>
            <span className="text-slate-500 text-xs ml-auto">{subAgentsOpen ? "▲" : "▼"}</span>
          </button>
          {subAgentsOpen && (
            <div className="space-y-2">
              {children.map((child) => (
                <Link
                  key={child.id}
                  to={`/runs/${child.id}`}
                  className="flex items-center gap-3 p-2 rounded border border-slate-700 hover:border-slate-600 hover:bg-slate-800 transition-colors"
                >
                  <StatusBadge status={child.status} />
                  <span className="text-sm text-slate-300 truncate flex-1 font-mono">{child.label}</span>
                  <span className="text-xs text-slate-500 font-mono shrink-0">{fmt(child.input_tokens + child.output_tokens)} tok</span>
                  <span className="text-xs text-slate-500 font-mono shrink-0">{duration(child.duration_seconds)}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

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

function repoBase(prUrl: string): string | null {
  const m = prUrl.match(/^(https:\/\/github\.com\/[^/]+\/[^/]+)\/pull\/\d+/);
  return m ? m[1] : null;
}

function prLabel(url: string): string {
  const m = url.match(/\/pull\/(\d+)$/);
  return m ? `PR #${m[1]}` : url;
}

function ticketUrl(ref: string): string | null {
  if (/^LINEAR-\d+$/i.test(ref)) return `https://linear.app/issue/${ref.toUpperCase()}`;
  if (/^[A-Z]+-\d+$/.test(ref)) return null; // Jira needs org URL — configurable later
  if (/^#\d+$/.test(ref)) return null;         // GitHub issue needs repo URL
  return null;
}
