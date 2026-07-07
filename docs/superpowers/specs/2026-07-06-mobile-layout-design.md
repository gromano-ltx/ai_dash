# AI-45: Mobile-friendly layout

## Context

The dashboard frontend loads on mobile browsers but isn't adapted for narrow screens. `Layout.tsx`'s
sidebar nav is a fixed 208px-wide column with no responsive collapse, eating over half the width on
a typical phone screen. Responsive Tailwind breakpoints (`sm:`/`md:`/`lg:`) appear in only one file
(`Dashboard.tsx`, for the stat-tile grid); `Runs.tsx`, `RunDetail.tsx`, and `Settings.tsx` have none.
The Runs table has no horizontal-scroll handling, so it either overflows awkwardly or squeezes
illegibly on narrow screens.

This spec covers making all four pages (Dashboard, Runs, RunDetail, Settings) usable at common phone
widths (~375-430px), without changing desktop behavior at `md` (768px) and above.

## Decisions made during brainstorming

- **Mobile nav pattern**: hamburger + slide-in drawer (not a bottom tab bar or a collapsed icon rail).
- **Breakpoint**: `md` (768px, Tailwind default). Phones and portrait tablets get the drawer;
  landscape tablets and desktop keep today's always-visible sidebar.
- **Runs table on mobile**: horizontal scroll (not a reflow to stacked cards), a smaller change that
  preserves the existing column layout and click-to-open-row behavior.
- **Drawer mechanism**: plain `useState` + Tailwind transform/transition classes, no new dependency
  (e.g. no Headless UI/Radix Dialog, no CSS-only checkbox hack), matching the existing codebase
  convention of plain hooks for toggle state (e.g. `RunDetail.tsx`'s `subAgentsOpen`).

## Architecture & components

**`Layout.tsx`** gains one new piece of state, `mobileNavOpen` (`useState<boolean>`), plus three
structural additions:

- **Mobile top bar** (`md:hidden`): a sticky header with a hamburger button (toggles `mobileNavOpen`)
  and the "ai_dash" title, shown only below `md`, since the sidebar itself is hidden by default
  there and something needs to carry the branding + toggle in that mode.
- **Sidebar becomes a drawer below `md`**: the existing `<aside>` gets `fixed inset-y-0 left-0 z-40
  transform transition-transform duration-200` plus `-translate-x-full` (closed) / `translate-x-0`
  (open) driven by `mobileNavOpen`, reverting to `md:relative md:translate-x-0 md:z-auto` (today's
  normal static layout) at `md` and above. Width stays `w-52`, unchanged from today; it's now an
  overlay instead of a permanent column, but the same width.
- **Backdrop**: a `fixed inset-0 bg-black/50 z-30 md:hidden` div, rendered only when `mobileNavOpen`
  is true, closing the drawer on click. Each `NavLink` also calls `setMobileNavOpen(false)` on click,
  so navigating to a new page auto-closes the drawer.

No new dependencies.

## Tables (Runs + Settings)

- **`Runs.tsx`**: wrap the existing `<table>` in a new `<div className="overflow-x-auto">` inside
  the current rounded container (the container's `overflow-hidden` still clips the rounded corners
  correctly; the inner div handles its own horizontal scroll independently). Give the table a
  `min-w-[720px]` so columns keep their current spacing instead of squeezing illegibly: the whole
  table scrolls sideways rather than any cell's content shrinking.
- **`Settings.tsx`**'s API-keys table gets the same `overflow-x-auto` wrapper for consistency, even
  though at 4 narrow columns it's less likely to actually need it in practice; it matches the ticket's
  "Runs table (and any other wide tables)" wording and costs nothing to add.

## Dashboard header wrap fix

Found during design review, not in the original ticket description but agreed to be in scope:
`Dashboard.tsx`'s header row (the "Overview" title + the 5 time-range buttons + the "N active"
badge) is a single non-wrapping `flex justify-between` row. At 375px width the buttons + badge
overflow past the title. Fix: add `flex-wrap gap-y-2` to that row so it wraps onto a second line on
narrow screens instead of overflowing.

## RunDetail.tsx

No structural changes expected: it already uses `max-w-3xl`, flex-wrap on its badge row, and a
`grid-cols-3` token breakdown with short numeric values that should fit comfortably at 375px.
Covered by the manual verification pass below rather than a planned code change; if verification
finds a real overflow here, treat it as a new finding, not a silent scope change.

## Error handling

None needed: this is a pure layout/CSS change with no new data flow, API calls, or failure modes.

## Testing

The frontend has no automated test suite (no test files, no test script in `package.json`);
verification here matches the rest of the codebase: `tsc -b` typecheck + `vite build` (already run
in CI's `checks` job), plus manual verification. Run `npm run dev` and, at each of these widths,
confirm:

- **375px, 430px** (target phone range): hamburger visible, sidebar hidden by default; tapping the
  hamburger opens the drawer with backdrop; tapping the backdrop or a nav link closes it; Runs table
  and Settings keys table scroll horizontally without squeezing; Dashboard header wraps onto two
  lines without overflow; RunDetail renders without overflow.
- **767px** (just below `md`): still in drawer mode.
- **768px and ~1280px** (desktop): full sidebar always visible, no hamburger, no regression from
  today's layout.

## Out of scope

- Reflowing the Runs table into stacked cards (horizontal scroll was the chosen approach).
- A bottom tab bar or collapsed icon-rail nav pattern (hamburger + drawer was the chosen approach).
- Any new UI library (Headless UI, Radix, etc.) for the drawer/dialog mechanics.
- Adding an automated test suite for the frontend: this spec follows the existing (manual)
  verification convention, not a new one.
