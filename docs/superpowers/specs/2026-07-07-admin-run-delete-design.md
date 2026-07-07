# Admin bulk-delete endpoint for AgentRun rows

## Context

Cleaning up mistaken or test ingests (e.g. the 15 real Gemini CLI sessions shipped to production
during AI-47's live verification) currently requires direct production database access; there's no
way to remove an `AgentRun` row through the app itself. Direct DB access to `ai-dash-db` is also
hard to reach in practice (private-IP-only Cloud SQL instance, no bastion/proxy set up), so this
kind of cleanup either needs infrastructure access most people don't have, or falls to whoever can
reach the instance directly.

This adds a proper, authenticated, admin-only path to delete `AgentRun` rows (and their associated
`TranscriptStore` transcript content) through the app's existing per-user auth system (AI-7),
instead of ad hoc production DB access.

## Endpoint

`DELETE /api/runs`, body `{"ids": ["<run-id>", ...]}`.

Gated by the existing `require_admin` dependency in `backend/auth.py`, the same dependency already
used by `/api/accounts`'s DELETE/PATCH routes in `backend/api/auth_routes.py`. Non-admins get 403;
unauthenticated callers get 401 (identical to those existing routes' behavior).

Added to `backend/api/routes.py` (where `AgentRun`/`TranscriptStore` and the other `/runs`/`/keys`
routes already live), not `auth_routes.py`: it operates on run data, not accounts, even though it
imports `require_admin` from the auth module.

## Cascade behavior

For each requested id:
1. Find any `AgentRun` rows with `parent_id` equal to that id (one level; subagent runs don't
   themselves have subagents in this schema, so no recursive lookup is needed) and delete them too.
2. Delete the requested `AgentRun` row itself.
3. Delete the `TranscriptStore` row for every deleted `AgentRun.id` (the requested id and any
   cascaded children's ids). Confirmed `TranscriptStore.session_id` always equals `AgentRun.id` for
   all three provider adapters: Claude Code's subagent file naming (`agent-<agentId>.jsonl`)
   produces a `path.stem` that already matches the `agent-{agent_id}` run-id convention, Codex has no
   subagents, and Gemini's subagent run id is the subagent's own already-unique session id, matching
   the collector's `X-Session-Id: path.stem` header exactly, so this is a direct id match in all
   cases, not a guess.

Without cascading, a deleted parent's children would silently reappear as unlinked top-level runs in
the dashboard (their `parent_id` would point at a row that no longer exists).

## Safety measures

- **Batch size cap:** 100 ids per call, returning 422 if exceeded: a hard sanity ceiling so a
  scripting mistake (e.g. accidentally passing every run id in the system) can't wipe the whole
  table in one request. This is a fixed constant, not configurable.
- **Exact-id-only:** the request body is a list of specific ids, with no filter, date-range, or
  provider-based bulk deletion. Every id is looked up individually; nothing this endpoint accepts
  can accidentally match more rows than what's explicitly listed.
- **Transparent result reporting:** the response separates ids that were actually found-and-deleted
  from ids that were requested but didn't exist, so a typo'd id is visible rather than silently
  no-op'd.
- **Audit log line:** logs which admin (`current.username`) deleted which ids and when, at `INFO`
  level, via the same `logging` setup the rest of the backend uses. `AgentRun` data is otherwise
  irrecoverable observability history (unlike an API key or account, which can be recreated), so a
  paper trail is worth it even though no other admin route in this codebase currently logs its
  actions.

## Response shape

```json
{
  "deleted": ["<id>", "..."],
  "not_found": ["<id>", "..."]
}
```

`deleted` includes cascaded children's ids, not just the ids the caller explicitly requested, so
the caller can see the full scope of what was actually removed.

## Testing plan

- Deleting a single existing id removes both its `AgentRun` and `TranscriptStore` rows.
- Deleting a parent id also removes its child (subagent) `AgentRun`/`TranscriptStore` rows, and
  those child ids appear in the response's `deleted` list even though they weren't in the request
  body.
- Requesting a mix of existing and nonexistent ids: existing ones are deleted, nonexistent ones are
  reported under `not_found`, and nothing errors.
- A request over the 100-id cap returns 422 and deletes nothing.
- A non-admin (logged-in but `is_admin=False`) gets 403; an unauthenticated request gets 401, both
  without deleting anything.
- The audit log line is emitted with the correct admin username and id list.

## Out of scope

- A frontend UI for this: this ticket is the backend endpoint only, callable via `curl`/API for
  now. A Settings-page "delete run" button could follow later if this becomes a recurring need.
- Soft-delete / undo: this is a hard delete, matching the existing `/accounts` and `/keys` DELETE
  endpoints' behavior in this codebase.
- A persistent, queryable audit trail (e.g. a dedicated audit-log table): the `INFO`-level log line
  is judged sufficient for this scope; a real audit table would be a separate ticket if deletions
  become frequent enough to need historical review.
