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
}

export interface DailyBucket {
  date: string;
  anthropic: number;
  openai: number;
  gemini: number;
  input_tokens: number;
  output_tokens: number;
}

export interface Stats {
  total_runs_7d: number;
  total_input_tokens_7d: number;
  total_output_tokens_7d: number;
  total_commits_7d: number;
  total_prs_7d: number;
  active_providers: string[];
  running_count: number;
}
