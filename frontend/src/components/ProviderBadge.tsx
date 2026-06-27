const STYLES: Record<string, string> = {
  anthropic: "bg-violet-500/20 text-violet-300 border border-violet-500/30",
  openai: "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30",
  gemini: "bg-blue-500/20 text-blue-300 border border-blue-500/30",
};

const LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
};

export function ProviderBadge({ provider }: { provider: string }) {
  return (
    <span className={`inline-flex px-2 py-0.5 rounded text-xs font-mono ${STYLES[provider] ?? "bg-slate-700 text-slate-400"}`}>
      {LABELS[provider] ?? provider}
    </span>
  );
}
