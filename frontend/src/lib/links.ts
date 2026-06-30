const LINEAR_WORKSPACE = "ai-dash";

export function ticketUrl(ref: string): string | null {
  if (/^[A-Z]{2,10}-\d+$/.test(ref))
    return `https://linear.app/${LINEAR_WORKSPACE}/issue/${ref}`;
  return null;
}

export function repoBase(prUrl: string): string | null {
  const m = prUrl.match(/^(https:\/\/github\.com\/[^/]+\/[^/]+)\/pull\/\d+/);
  return m ? m[1] : null;
}

export function prLabel(url: string): string {
  const m = url.match(/\/pull\/(\d+)$/);
  return m ? `PR #${m[1]}` : url;
}

export function commitUrl(hash: string, githubRepo?: string | null, prUrls?: string[]): string | null {
  const base = githubRepo ?? (prUrls?.length ? repoBase(prUrls[0]) : null);
  return base ? `${base}/commit/${hash}` : null;
}
