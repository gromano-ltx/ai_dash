import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useRun, useRunChildren } from "../lib/api";
import { StatusBadge } from "../components/StatusBadge";
import { ProviderBadge } from "../components/ProviderBadge";
import { fmt, duration } from "../lib/format";
import { ticketUrl, prLabel, commitUrl } from "../lib/links";

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: run, isLoading } = useRun(id!);
  const { data: children } = useRunChildren(id!);
  const [subAgentsOpen, setSubAgentsOpen] = useState(true);

  if (isLoading) return <div className="p-6 text-ledger-faint font-mono text-sm">loading…</div>;
  if (!run) return <div className="p-6 text-ledger-faint font-mono text-sm">run not found</div>;

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <button
        onClick={() => navigate(-1)}
        className="text-xs font-mono text-ledger-faint hover:text-ledger-dim transition-colors"
      >
        ← back
      </button>

      <div className="space-y-2">
        <h1 className="text-lg font-sans font-semibold text-ledger-ink">{run.label}</h1>
        <div className="flex flex-wrap items-center gap-3">
          <ProviderBadge provider={run.provider} />
          <StatusBadge status={run.status} />
          <span className="text-xs font-mono text-ledger-faint">{run.model}</span>
          {run.user && <span className="text-xs font-mono text-ledger-faint">by {run.user}</span>}
          <span className="text-xs font-mono text-ledger-faint tabular-nums">{duration(run.duration_seconds)}</span>
        </div>
        {run.ticket_refs.length > 0 && (
          <div className="flex gap-3 flex-wrap">
            {run.ticket_refs.map((t) => {
              const url = ticketUrl(t);
              return url ? (
                <a key={t} href={url} target="_blank" rel="noopener noreferrer"
                  className="text-xs font-mono uppercase tracking-wider text-ledger-accent border-b border-ledger-accent/50 hover:border-ledger-accent transition-colors pb-0.5">
                  {t}
                </a>
              ) : (
                <span key={t} className="text-xs font-mono uppercase tracking-wider text-ledger-accent border-b border-ledger-accent/50 pb-0.5">
                  {t}
                </span>
              );
            })}
          </div>
        )}
      </div>

      {run.task_description && (
        <div className="bg-ledger-surface border border-ledger-rule p-4">
          <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider mb-2">Task</p>
          <p className="text-sm text-ledger-ink">{run.task_description}</p>
        </div>
      )}

      <div className="bg-ledger-surface border border-ledger-rule p-4 space-y-3">
        <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider">Activity Timeline</p>
        <div className="space-y-2 text-sm">
          <TimelineItem icon="◎" label="Task started" detail={new Date(run.started_at).toLocaleString()} />
          {run.git_commits.map((hash) => (
            <TimelineItem key={hash} icon="⬡" label="Commit" detail={hash} mono
              link={commitUrl(hash, run.meta?.github_repo, run.git_prs) ?? undefined} />
          ))}
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
        <div className="bg-ledger-surface border border-ledger-rule p-4 space-y-3">
          <button
            onClick={() => setSubAgentsOpen((o) => !o)}
            className="flex items-center gap-2 w-full text-left"
          >
            <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider">
              Sub-agents ({children.length})
            </p>
            <span className="text-ledger-faint text-xs ml-auto">{subAgentsOpen ? "▲" : "▼"}</span>
          </button>
          {subAgentsOpen && (
            <div className="space-y-2">
              {children.map((child) => (
                <Link
                  key={child.id}
                  to={`/runs/${child.id}`}
                  className="flex items-center gap-3 p-2 border border-ledger-rule hover:border-ledger-faint hover:bg-ledger-raised transition-colors"
                >
                  <StatusBadge status={child.status} />
                  <span className="text-sm font-sans text-ledger-ink truncate flex-1">{child.label}</span>
                  <span className="text-xs font-mono text-ledger-faint shrink-0 tabular-nums">{fmt(child.input_tokens + child.output_tokens)} tok</span>
                  <span className="text-xs font-mono text-ledger-faint shrink-0 tabular-nums">{duration(child.duration_seconds)}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="bg-ledger-surface border border-ledger-rule p-4">
        <p className="text-xs text-ledger-faint font-sans uppercase tracking-wider mb-3">Tokens</p>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <p className="text-xl font-mono font-medium text-ledger-ink tabular-nums">{fmt(run.input_tokens)}</p>
            <p className="text-xs font-sans text-ledger-faint mt-0.5">input</p>
          </div>
          <div>
            <p className="text-xl font-mono font-medium text-ledger-ink tabular-nums">{fmt(run.output_tokens)}</p>
            <p className="text-xs font-sans text-ledger-faint mt-0.5">output</p>
          </div>
          <div>
            <p className="text-xl font-mono font-medium text-ledger-ink tabular-nums">{fmt(run.input_tokens + run.output_tokens)}</p>
            <p className="text-xs font-sans text-ledger-faint mt-0.5">total</p>
          </div>
        </div>
        <div className="mt-3 flex h-1.5">
          <div
            className="bg-ledger-accent"
            style={{ width: `${(run.input_tokens / (run.input_tokens + run.output_tokens)) * 100}%` }}
          />
          <div className="flex-1 bg-ledger-amber" />
        </div>
        <div className="flex justify-between text-xs font-sans text-ledger-faint mt-1">
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
      <span className="text-ledger-faint w-4 shrink-0 mt-0.5">{icon}</span>
      <span className="text-ledger-dim font-sans w-24 shrink-0">{label}</span>
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer" className="text-provider-gemini hover:underline truncate text-xs font-mono">
          {detail}
        </a>
      ) : (
        <span className={`text-ledger-ink truncate ${mono ? "font-mono text-xs" : "font-sans"}`}>{detail}</span>
      )}
    </div>
  );
}
