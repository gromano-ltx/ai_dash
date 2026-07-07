const COLORS: Record<string, string> = {
  anthropic: "text-provider-anthropic border-provider-anthropic/50",
  openai: "text-provider-openai border-provider-openai/50",
  gemini: "text-provider-gemini border-provider-gemini/50",
};

const LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  gemini: "Gemini",
};

export function ProviderBadge({ provider }: { provider: string }) {
  return (
    <span
      className={`inline-block px-1.5 pb-0.5 text-xs font-mono uppercase tracking-wider border-b ${
        COLORS[provider] ?? "text-ledger-faint border-ledger-rule"
      }`}
    >
      {LABELS[provider] ?? provider}
    </span>
  );
}
