const LINEAR_WORKSPACE = "ai-dash";

export function ticketUrl(ref: string): string | null {
  // Only the "PREFIX-123" shape (backend's TICKET_RE) is a Linear ticket.
  // Bare "#123" refs are GitHub issue numbers, extracted by the same backend
  // regex, but there's no repo context available at this ref-string level to
  // build a valid issue URL — callers render those as plain, unlinked text.
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
  // github_repo (meta) isn't always captured — e.g. no `git remote`/`gh pr`
  // output was seen in the transcript — so fall back to deriving the repo
  // from a PR URL the run did produce, since both point at the same repo.
  const base = githubRepo ?? (prUrls?.length ? repoBase(prUrls[0]) : null);
  return base ? `${base}/commit/${hash}` : null;
}
