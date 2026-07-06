# AI-7: Auth — per-user login to replace shared dashboard password

## Context

Today the entire dashboard is gated by a single shared secret: `backend/main.py` runs an ASGI
middleware that checks `DASHBOARD_PASSWORD` via HTTP Basic Auth (the username half of the
Basic Auth pair is decoded but discarded — only the password is checked, with a plain `==`
compare). There's no login page; the browser's native Basic Auth prompt is the only "login UI".
If `DASHBOARD_PASSWORD` is unset, auth is disabled entirely (today's local single-user dev mode).

There is no `users` table and no identity model anywhere in the schema. "User" is a plain
free-text string: `AgentRun.user` and `ApiKey.user` (`backend/models.py`) are just labels typed
in when an API key is created in Settings. The sidebar's "user switcher"
(`frontend/src/lib/UserContext.tsx` + `frontend/src/components/Layout.tsx`) is a client-side
`localStorage`-backed filter with **no server-side enforcement** — any client can already view
any user's runs by changing the dropdown or the `user` query param directly.

This ticket replaces that shared password with real per-user accounts, session-based login, and
(per clarification during design) introduces actual per-user data scoping — the first real
access-control boundary this dashboard has had.

### DoD (from Linear AI-7)
- Login page at `/login` with username + password form
- Session token (JWT or signed cookie) issued on success, expires in 30 days
- User identity derived from session (replaces global user switcher)
- Admin can create/revoke user accounts from Settings
- Existing API key auth for ingest endpoint unchanged
- Shared `DASHBOARD_PASSWORD` env var still works as fallback for single-user deploys

## Key design decisions

These were the open forks resolved during brainstorming; each has real alternatives, so they're
called out explicitly rather than left implicit in the architecture below.

1. **Data scoping**: non-admin users see only their own runs (`AgentRun.user == session.username`).
   Admins see all runs (override). This goes beyond the DoD's literal wording but was chosen
   because the current "switcher" behavior (anyone sees everyone's data) has no real access
   control today, and per-user login without scoping would be a fairly hollow "login" feature.
2. **Identity linkage**: the new `users.username` is the *same free-text string* already used as
   `AgentRun.user` / `ApiKey.user` — no new ID concept, no backfill migration. An admin must
   create an account with the exact same spelling/casing already used for that user's API key.
3. **Session mechanism**: signed `httponly` cookie (via `itsdangerous`), not JWT. No server-side
   session store — the cookie is self-verifying (signature + embedded timestamp, 30-day max-age).
   Chosen because it doesn't require the frontend to wire an `Authorization` header into every
   TanStack Query fetch call, and it degrades similarly to today's cookie-free Basic Auth prompt.
4. **Password fallback semantics**: `DASHBOARD_PASSWORD` Basic Auth only applies while the
   `users` table is empty. As soon as the first account is created, Basic Auth is retired for
   that deployment and only cookie-session login works. This is a one-way cutover, not a
   permanent dual-mode system.
5. **Admin bootstrap**: the first account ever created is auto-flagged `is_admin=True`. Multiple
   admins are allowed afterward; any admin can create accounts and toggle the admin flag on
   others, except that the last remaining admin cannot be demoted or deleted (would lock out
   account management entirely).
6. **API key management becomes admin-only**: since an API key's `user` label determines which
   scoping bucket ingested runs land in, letting a non-admin mint a key for an arbitrary username
   would undermine the new scoping. The existing "API Keys" Settings section becomes admin-only,
   alongside the new "Users" section.
7. **Implementation approach**: hand-rolled (`passlib[bcrypt]` for hashing, `itsdangerous` for
   signed cookies) rather than adopting an auth framework (e.g. `fastapi-users`). This matches
   the codebase's existing minimal, hand-rolled style (no Alembic, hand-rolled `_seed()` /
   `_add_missing_columns()` migrations) and avoids pulling in framework opinions (password reset
   flows, OAuth hooks, Alembic-based migrations) that aren't in the DoD.

## Data model

New table in `backend/models.py`, created automatically via the existing
`SQLModel.metadata.create_all(engine)` in `init_db()` — no Alembic, no manual `ALTER TABLE`
needed since this is a net-new table, not an added column on an existing one.

```python
class User(SQLModel, table=True):
    __tablename__ = "users"
    username: str = Field(primary_key=True)   # matches existing AgentRun.user / ApiKey.user strings
    password_hash: str
    is_admin: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

No email, reset tokens, or last-login tracking — none of that is in scope.

## Auth flow / backend enforcement

**New endpoints** (`backend/api/auth.py`, new file):
- `POST /api/login` — `{username, password}` → looks up `User`, verifies with
  `passlib.hash.bcrypt.verify()`. Success sets the session cookie described below. Failure →
  `401` with a generic "invalid username or password" (don't reveal which field was wrong).
- `POST /api/logout` — clears the cookie.
- `GET /api/me` — returns `{username, is_admin}` for the current session, or `401` if none.
  Used by the frontend to know who's logged in and to detect "logged out" state.

**Session cookie**: `itsdangerous.URLSafeTimedSerializer`, keyed by a new `SESSION_SECRET` env
var (separate secret from `DASHBOARD_PASSWORD`). Payload is `{"username": ...}`. Cookie flags:
`httponly`, `secure`, `samesite=lax`. Verified with `max_age=30 days` per the DoD's expiry
requirement — an expired or tampered cookie simply fails verification; there's no server-side
session table to separately expire or revoke, so revocation (e.g. deleting a user account) takes
effect the next time that cookie is verified against a since-deleted `User` row.

**Middleware** (`backend/main.py`, replacing today's single-branch Basic Auth check): split into
three checks, evaluated in order:
1. **Public path** (`/install.sh`, `/collector.py`, `/api/v1/ingest`, `/api/login`, and the
   `/login` frontend route) → pass through unchanged, same as today's `_PUBLIC_PATHS`.
2. **No `User` rows exist** → fall back to today's `DASHBOARD_PASSWORD` Basic Auth check,
   byte-for-byte the current behavior.
3. **Otherwise** → require a valid signed session cookie. Verify signature + max-age, then load
   the `User` row by the embedded username (confirms the account still exists / wasn't revoked
   since the cookie was issued) and attach it to `request.state.user`. Missing, invalid, or
   expired cookie → `401`.

**Scoping enforcement**: a shared helper (e.g. `scope_to_user(query, user)` in
`backend/api/routes.py`) applied to the run-listing endpoints (`/api/runs`, `/api/stats`,
`/api/daily`, `/api/stream`, `/api/users`) — adds `.where(AgentRun.user == user.username)`
unless `user.is_admin`. Single-run detail (`/api/runs/:id`) applies the same check but returns
`404` (not `403`) when a non-admin requests a run they don't own, to avoid confirming the run's
existence to someone who shouldn't see it.

**Admin-only routes**: new user-management endpoints (`POST /api/users`,
`DELETE /api/users/{username}`, `PATCH /api/users/{username}` for the admin toggle) plus the
existing `/api/keys` endpoints all require `request.state.user.is_admin`, else `403`. Creating a
user with a username that already exists → `409`. Demoting or deleting the last remaining admin
→ `400`.

## Frontend

- **`/login` page** (new `frontend/src/pages/Login.tsx`): username + password form, posts to
  `/api/login` with `credentials: 'include'`, redirects to `/` on success. Added as a top-level
  route in `App.tsx`, outside `<Layout>` (no sidebar/nav on the login screen).
- **Session-aware layout**: `<Layout>` queries `GET /api/me` on mount. A `401` from `/api/me` (or
  from any API call — add a shared TanStack Query error handler) redirects to `/login`. This
  replaces the browser's native Basic Auth prompt as the "you're logged out" signal.
- **Remove the global user switcher**: `UserContext.tsx` and the sidebar `<select>` in
  `Layout.tsx` are deleted. "Current user" is whatever `/api/me` returns — no client-side
  override. `Dashboard.tsx` / `Runs.tsx` drop their `effectiveUser = user || globalUser` logic;
  backend scoping now does the filtering, so non-admin clients don't need to pass a `user` query
  param at all.
- **Admin "view as" filter**: admins keep a `/runs` filter dropdown (populated from
  `/api/users`), in the same UI slot the old switcher occupied — but now it's a filter over data
  the admin can already see, not an identity switch. Non-admins don't see this control.
- **Settings page** (`frontend/src/pages/Settings.tsx`): add a new "Users" section next to the
  existing "API Keys" section — table of accounts (username, admin badge, created date) with
  create/revoke/toggle-admin actions. Both the new "Users" section and the existing "API Keys"
  section are admin-only (hidden, and backend-enforced via `403`, for non-admins).
- **Logout**: a button in the sidebar (replacing the old switcher's position), calling
  `POST /api/logout` then redirecting to `/login`.

## Migration & rollout

- No data migration for existing `AgentRun` / `ApiKey` rows — they keep using their free-text
  `user` string as-is; matching `User` accounts are created after the fact.
- New env var `SESSION_SECRET` needs to be added to Terraform (`infra/main.tf`, alongside the
  existing `DASHBOARD_PASSWORD` / `DATABASE_URL` secrets) and to local `.env.example`.
  `DASHBOARD_PASSWORD` config itself is untouched.
- New Python dependencies: `passlib[bcrypt]`, `itsdangerous` (added to `pyproject.toml`).
- Rollout sequence for the live Cloud Run deployment: ships with `users` empty → shared-password
  Basic Auth keeps working exactly as today → log in with `DASHBOARD_PASSWORD`, go to Settings,
  create an account matching the existing seeded API-key user (`Gabby`, from `db.py`'s `_seed()`)
  → that account is auto-admin → Basic Auth is now retired for this deployment.

## Error handling

- Wrong credentials on `/api/login` → `401`, generic message.
- Missing/invalid/expired session cookie on a protected route → `401` → frontend redirects to
  `/login`.
- Non-admin hitting an admin-only route → `403`.
- Non-admin requesting a run they don't own → `404`.
- Duplicate username on account creation → `409`.
- Removing the last remaining admin's admin flag or account → `400`.

## Testing

- **Backend**: unit tests for each of the three middleware branches (public path,
  password-fallback mode, session-cookie mode); the scoping helper (admin sees all, non-admin
  sees only their own runs); admin-gated routes returning `403` for non-admins; the last-admin
  guard.
- **Frontend**: login form success (redirect) and failure (error message) paths; a protected
  route redirecting to `/login` on a `401` from `/api/me`.
- **Manual verification**: fresh DB (no users) still authenticates via `DASHBOARD_PASSWORD`
  exactly as today; after creating the first account, confirm Basic Auth is rejected and only
  cookie-session login works; confirm a non-admin account cannot see another user's runs or reach
  Settings' admin sections.

## Out of scope

- Password reset / forgot-password flow.
- OAuth / SSO.
- Server-side session revocation list (revocation works by deleting the `User` row, which the
  session-cookie check picks up on next verification — not instant, bounded by normal request
  frequency, not a ticket requirement).
- Any change to ingest (`POST /api/v1/ingest`) or `ApiKey` auth beyond restricting its
  *management* UI to admins — the ingest auth mechanism itself is unchanged, per the DoD.
