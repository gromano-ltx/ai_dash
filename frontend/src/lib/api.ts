import { useQuery } from "@tanstack/react-query";
import type { AgentRun, Stats, DailyBucket } from "./types";

const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export function useRuns(filters?: {
  provider?: string;
  status?: string;
  user?: string;
  ticket?: string;
}) {
  const params = new URLSearchParams();
  if (filters?.provider) params.set("provider", filters.provider);
  if (filters?.status) params.set("status", filters.status);
  if (filters?.user) params.set("user", filters.user);
  if (filters?.ticket) params.set("ticket", filters.ticket);
  const qs = params.toString();
  return useQuery<AgentRun[]>({
    queryKey: ["runs", filters],
    queryFn: () => get(`/runs${qs ? `?${qs}` : ""}`),
    refetchInterval: 5000,
  });
}

export function useRun(id: string) {
  return useQuery<AgentRun>({
    queryKey: ["run", id],
    queryFn: () => get(`/runs/${id}`),
  });
}

export function useRunChildren(parentId: string) {
  return useQuery<AgentRun[]>({
    queryKey: ["run-children", parentId],
    queryFn: () => get(`/runs?parent_id=${parentId}`),
  });
}

export function useDaily(user?: string) {
  const qs = user ? `?user=${encodeURIComponent(user)}` : "";
  return useQuery<DailyBucket[]>({
    queryKey: ["daily", user],
    queryFn: () => get(`/daily${qs}`),
    refetchInterval: 30000,
  });
}

export function useUsers() {
  return useQuery<{ users: string[] }>({
    queryKey: ["users"],
    queryFn: () => get("/users"),
  });
}

export function useStats(user?: string) {
  const qs = user ? `?user=${encodeURIComponent(user)}` : "";
  return useQuery<Stats>({
    queryKey: ["stats", user],
    queryFn: () => get(`/stats${qs}`),
    refetchInterval: 10000,
  });
}
