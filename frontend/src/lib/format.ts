export function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function duration(secs: number | null): string {
  // == null (not a falsy check) so a real 0-second duration renders as "0s"
  // instead of being treated the same as a missing value.
  if (secs == null) return "—";
  // Round once up front, then only floor/modulo below — rounding each
  // component separately can carry over (e.g. 59.6m rounding to "60m").
  const total = Math.round(secs);
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`;
  if (total < 86400) {
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    return `${h}h ${m}m`;
  }
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  return `${d}d ${h}h`;
}
