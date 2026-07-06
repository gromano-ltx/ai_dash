# Mobile-Friendly Layout (AI-45) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard frontend (Dashboard, Runs, RunDetail, Settings) usable at common phone widths (~375-430px) without changing desktop behavior at `md` (768px) and above.

**Architecture:** `Layout.tsx`'s sidebar becomes a hamburger-triggered slide-in drawer below `md`, driven by plain `useState` + Tailwind transform/transition classes (no new dependency). The Runs and Settings tables get a horizontal-scroll wrapper instead of reflowing. Dashboard's header row gets a wrap fix for its 5 time-range buttons.

**Tech Stack:** React 18 + TypeScript + Tailwind CSS (existing stack, no additions).

## Global Constraints

- Breakpoint for the mobile nav switch: `md` (768px, Tailwind default) — copied verbatim from the spec.
- No new dependencies (no Headless UI, Radix, or similar dialog library) — plain `useState` + Tailwind only.
- Desktop behavior at `md` and above must be visually unchanged from today.
- No automated test suite exists for the frontend (no test files, no test script in `package.json`) — verification is `tsc -b` (typecheck) + `vite build`, both already run in CI, plus manual browser verification at the widths listed in each task.

---

### Task 1: Layout.tsx — hamburger + slide-in drawer nav

**Files:**
- Modify: `frontend/src/components/Layout.tsx` (entire file — see below)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by other tasks — Tasks 2 and 3 touch unrelated files.

- [ ] **Step 1: Replace the entire contents of `frontend/src/components/Layout.tsx`**

```tsx
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useUsers } from "../lib/api";
import { useActiveUser } from "../lib/UserContext";

const nav = [
  { to: "/", label: "Overview", icon: "⬡" },
  { to: "/runs", label: "Runs", icon: "▶" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

export function Layout() {
  const { data: usersData } = useUsers();
  const { user, setUser } = useActiveUser();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-52 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col transform transition-transform duration-200 md:relative md:translate-x-0 md:z-auto ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="px-5 py-4 border-b border-slate-800">
          <span className="text-sm font-mono font-semibold text-slate-100 tracking-wide">ai_dash</span>
          <span className="ml-2 text-xs text-slate-500">v0.1</span>
        </div>
        <div className="px-3 py-2.5 border-b border-slate-800">
          <label className="text-xs text-slate-500 font-mono block mb-1">user:</label>
          <select
            value={user}
            onChange={(e) => setUser(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 text-slate-300 text-xs font-mono rounded px-2 py-1 focus:outline-none focus:border-slate-500"
          >
            <option value="">All users</option>
            {usersData?.users.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {nav.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMobileNavOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-slate-800 text-slate-100"
                    : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/50"
                }`
              }
            >
              <span className="text-xs opacity-60">{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {mobileNavOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-30 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="md:hidden sticky top-0 z-10 flex items-center gap-3 px-4 py-3 bg-slate-900 border-b border-slate-800">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            className="text-slate-300 text-xl leading-none"
          >
            ☰
          </button>
          <span className="text-sm font-mono font-semibold text-slate-100 tracking-wide">ai_dash</span>
        </header>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

This is the same component as before with three changes: `mobileNavOpen` state; the `<aside>` is now
fixed-positioned and slides in/out below `md` (reverting to the original static layout at `md` and
above via `md:relative md:translate-x-0 md:z-auto`), with each `NavLink` closing it on click; and a
new `md:hidden` mobile top bar (hamburger + title) plus backdrop, both absent above `md`.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit` (or `npm run build`, which runs `tsc -b` first)
Expected: no errors.

- [ ] **Step 3: Manual verification**

Run: `cd frontend && npm run dev`, open the printed local URL in a browser, and use devtools'
device toolbar (or resize the window) to check:

- **375px and 430px width**: sidebar is hidden by default; a hamburger + "ai_dash" title bar is
  visible at the top; clicking the hamburger slides the sidebar in from the left with a dark
  backdrop behind it; clicking the backdrop closes it; clicking a nav link (e.g. "Runs") navigates
  and closes the drawer.
- **767px width**: still in drawer mode (hamburger visible, sidebar hidden by default).
- **768px and ~1280px width**: sidebar is always visible on the left as a static column, exactly as
  before this change; no hamburger bar is shown; no visual regression from the pre-change layout.

Expected: all of the above hold at each width.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Layout.tsx
git commit -m "feat: hamburger + slide-in drawer nav for mobile (AI-45)"
```

---

### Task 2: Runs.tsx + Settings.tsx — horizontal-scroll tables

**Files:**
- Modify: `frontend/src/pages/Runs.tsx` (table wrapper only — two boundary edits)
- Modify: `frontend/src/pages/Settings.tsx` (table wrapper only — two boundary edits)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by other tasks.

- [ ] **Step 1: Wrap the Runs table in a horizontal-scroll container**

In `frontend/src/pages/Runs.tsx`, find this exact text (the table's opening tags):

```tsx
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
```

Replace it with:

```tsx
      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
```

Then find this exact text (the table's closing tags, unchanged from today):

```tsx
          </tbody>
        </table>
      </div>
```

Replace it with:

```tsx
          </tbody>
          </table>
        </div>
      </div>
```

Nothing between the `<thead>` and `</tbody>` needs to change — leave the interior of the table (all
column headers and row rendering) exactly as it is. The interior lines will be under-indented by two
spaces relative to their new nesting depth; this is cosmetic only (JSX doesn't require consistent
indentation to function) and does not need to be fixed — leave it as-is to keep this a minimal,
surgical change.

- [ ] **Step 2: Wrap the Settings API-keys table in a horizontal-scroll container**

In `frontend/src/pages/Settings.tsx`, find this exact text:

```tsx
        <div className="border border-slate-800 rounded overflow-hidden">
          <table className="w-full text-sm font-mono">
```

Replace it with:

```tsx
        <div className="border border-slate-800 rounded overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-sm font-mono">
```

Then find this exact text:

```tsx
            </tbody>
          </table>
        </div>
      </section>
```

Replace it with:

```tsx
            </tbody>
            </table>
          </div>
        </div>
      </section>
```

Same note as Step 1 — the interior (thead/tbody content) is unchanged; leave its indentation as-is.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 4: Manual verification**

Run: `cd frontend && npm run dev`, open the app, navigate to `/runs` and `/settings`, and at
**375px width** confirm:

- The Runs table's 9 columns (Task, Provider, Model, User, Status, Duration, Tokens, Ticket, Code)
  do not squeeze illegibly — the table itself scrolls horizontally within its rounded container
  (drag/swipe to see columns off-screen), and clicking a visible row still navigates to
  `/runs/:id` (the click handler is on the `<tr>`, unaffected by the new wrapper).
- The Settings API-keys table (Key Prefix, User, Created, delete button) similarly does not squeeze
  illegibly.
- At **768px and ~1280px width**, both tables look exactly as they did before this change (no
  visible horizontal scrollbar needed at those widths, since the content already fits).

Expected: all of the above hold.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Runs.tsx frontend/src/pages/Settings.tsx
git commit -m "feat: horizontal-scroll wrapper for Runs and Settings tables on mobile (AI-45)"
```

---

### Task 3: Dashboard.tsx — header row wrap fix

**Files:**
- Modify: `frontend/src/pages/Dashboard.tsx:109`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by other tasks.

- [ ] **Step 1: Make the header row wrap on narrow screens**

Find this exact line:

```tsx
      <div className="flex items-center justify-between">
```

Replace it with:

```tsx
      <div className="flex items-center justify-between flex-wrap gap-y-2">
```

This is the row containing the "Overview" title (left) and the 5 time-range buttons + "N active"
badge (right) — `flex-wrap` lets the right-hand cluster wrap onto a second line instead of
overflowing past the title, and `gap-y-2` adds vertical spacing between the two lines when wrapped.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual verification**

Run: `cd frontend && npm run dev`, open the app at `/` (Dashboard), and at **375px width** confirm:

- The "Overview" title and the 5 time-range buttons (24h/7d/30d/90d/All) no longer overflow
  horizontally — the buttons wrap onto a second line below the title if they don't fit on one line.
- If any runs are currently `running` (the "N active" badge), it wraps along with the buttons rather
  than being clipped off-screen.
- At **768px and ~1280px width**, the row looks exactly as it did before this change (single line,
  no wrapping, since there's enough width).

Expected: all of the above hold.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
git commit -m "fix: wrap Dashboard header row on narrow screens (AI-45)"
```

---

## Self-Review

**Spec coverage:** Mobile nav pattern (hamburger + drawer, `md` breakpoint, no new dependency) →
Task 1. Runs table horizontal scroll → Task 2 Step 1. Settings table horizontal scroll (spec's
"any other wide tables") → Task 2 Step 2. Dashboard header wrap fix (found during design review,
confirmed in scope) → Task 3. RunDetail.tsx — spec explicitly says no structural changes expected,
covered by manual verification only, no code task needed. Testing approach (manual, at 375/430/767/
768/~1280px, `tsc -b` + `vite build` as the automated baseline) → each task's Step 2-3 (or 2-4 for
Task 2). All spec sections have a corresponding task or explicit no-change rationale.

**Placeholder scan:** No TBD/TODO; every step shows complete code or exact find/replace text with
expected results.

**Type consistency:** `mobileNavOpen` (boolean state) and `setMobileNavOpen` are defined and used
only within Task 1's single file — no other task references them. No shared types/interfaces span
tasks, since all three tasks are independent, single-file, non-interacting UI tweaks.
