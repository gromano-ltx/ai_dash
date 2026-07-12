export interface AgentRun {
  id: string;
  provider: "anthropic" | "openai" | "gemini";
  model: string;
  status: "running" | "done" | "failed";
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  input_tokens: number;
  output_tokens: number;
  label: string;
  task_description: string | null;
  user: string | null;
  git_commits: string[];
  git_prs: string[];
  ticket_refs: string[];
  parent_id: string | null;
  meta: { github_repo?: string; git_branch?: string; [key: string]: unknown };
  estimated_input_cost_usd: number | null;
  estimated_output_cost_usd: number | null;
  estimated_cost_usd: number | null;
}

export interface DailyBucket {
  date: string;
  anthropic: number;
  openai: number;
  gemini: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ProviderStats {
  runs: number;
  input_tokens: number;
  output_tokens: number;
  commits: number;
}

export interface Stats {
  total_runs_7d: number;
  total_input_tokens_7d: number;
  total_output_tokens_7d: number;
  total_commits_7d: number;
  total_prs_7d: number;
  total_cost_usd: number;
  active_providers: string[];
  running_count: number;
  by_provider: Record<string, ProviderStats>;
  // AI-48: null when GITHUB_TOKEN isn't configured, or no PR has resolved
  // (merged/closed) yet in the selected window.
  pr_merge_success_rate: number | null;
  pr_merge_success_merged: number;
  pr_merge_success_resolved: number;
}

export interface Me {
  username: string | null;
  is_admin: boolean;
}
