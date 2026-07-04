---
name: linear-ticket
description: Create or update Linear tickets for the ai_dash project in the correct workspace/team/project with the house description structure. Trigger when asked to "create a linear ticket", "file a ticket", "open an issue in Linear", or similar, for work in this repo.
---

# Linear ticket creation (ai_dash)

## Auth — which key to use

There are two Linear API keys on this machine. **Only use the one allowlisted in
`.claude/settings.local.json`** (look for the `Bash(export LINEAR_API_KEY=...)` entry there and
run that exact command). The key in the plain shell env var (`$LINEAR_API_KEY` before you export
the one above) belongs to a different Linear workspace (`hybrid_llm`) used by another agent — never
use it for this project, and never overwrite or read `backend/.env` to find it (that's a separate,
denied path).

Do not hardcode the raw key value into any file that gets committed — pull it from
`.claude/settings.local.json` (gitignored) at run time, e.g.:

```bash
export LINEAR_API_KEY=$(grep -o 'lin_api_[A-Za-z0-9]*' .claude/settings.local.json | head -1)
```

Since the Bash tool does not persist shell state between tool calls, `export` and the `curl` call
that uses it must be in the **same** Bash invocation.

## Workspace IDs (ai-dash / org `ai_dash`)

- Team `AI` (key `AI`, workspace slug `ai-dash`): `ee86a96a-d5ac-4bf9-8388-9278b87950c9`
- Project `AI-dash`: `10cd1203-0cd5-4768-bfe9-66009c7309e1`
- States: Backlog `1aa579fb-eb67-4d27-9fb2-8e49ee0d72d1` · Todo `e9859f66-3e04-4f95-a8db-a03a172b88da` ·
  In Progress `e4b8d834-a381-437b-a416-9fa612fcbc03` · In Review `99b3273f-8a04-4279-9ffa-2eed8fdf5d56` ·
  Done `bc517435-c8b0-4402-a2b4-054ff093bb14` · Canceled `baa3456b-4400-4d89-bea2-b689727364f0` ·
  Duplicate `fdd4b820-4b63-4c24-8942-0bae3a1b4ccb`

If any ID looks stale (mutation fails, or team/project 404s), re-verify with:
```
{ organization { teams(first: 50) { nodes { name key id states { nodes { id name type } } } } } }
{ projects(first: 50) { nodes { name id } } }
```

Every new issue must set `teamId` and `projectId` to the values above.

## Default field values

- **stateId**: Todo, unless the user explicitly says the work is already in progress. Don't switch
  to In Progress just because you're about to implement it in the same session — this project's
  convention is that "in progress" reflects an active work session on the ticket, decided by the
  user, not by ticket-creation timing.
- **priority**: `2` (High) by default — matches most existing tickets (features, infra). Use `3`
  (Medium) for small cleanups/one-off fixes (e.g. AI-9, AI-19), and `4` (Low) for minor
  config/nice-to-have items (e.g. AI-8). Ask if genuinely unsure.
- **labels**: none — this workspace doesn't use labels currently.
- **estimate**: none.

## Title structure

Two accepted styles, matching existing tickets:

- `<Area>: <short description>` — for feature/UI-scoped work, e.g. `Cost tracking: show estimated
  $ spend per run and on dashboard`, `Runs table: merge PR and Commits columns into single Code
  column`.
- Plain imperative sentence — for infra/process-level work, e.g. `Set up automatic deploy to Cloud
  Run on merge to main`, `Add backend pytest test suite for API + ingest logic`.

Pick whichever reads more naturally; area-prefixed is the more common case.

## Description structure

Every ticket uses the same three sections, in this order:

```markdown
## Description

<what's broken or missing today, in user-facing terms — the "why", not the implementation>

## Objectives

<what this ticket needs to achieve; call out any open decisions still needing user sign-off>

## DoD

* <bullet>
* <bullet>
```

`DoD` bullets are plain `*` bullets (not `- [ ]` checkboxes). Keep `Description` and `Objectives`
each to a short paragraph or two — this is a ticket, not a design doc.

## Example mutation

```bash
export LINEAR_API_KEY=$(grep -o 'lin_api_[A-Za-z0-9]*' .claude/settings.local.json | head -1)
curl -s https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" -H "Content-Type: application/json" \
  --data '{
    "query": "mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { identifier url } } }",
    "variables": { "input": {
      "teamId": "ee86a96a-d5ac-4bf9-8388-9278b87950c9",
      "projectId": "10cd1203-0cd5-4768-bfe9-66009c7309e1",
      "stateId": "e9859f66-3e04-4f95-a8db-a03a172b88da",
      "title": "...",
      "description": "...",
      "priority": 2
    } }
  }'
```

After creating, report the returned `identifier` and `url` back to the user.
