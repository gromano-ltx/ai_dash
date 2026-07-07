import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useMe, logout } from "../lib/api";

const nav = [
  { to: "/", label: "Overview" },
  { to: "/runs", label: "Runs" },
  { to: "/settings", label: "Settings" },
];

export function Layout() {
  const { data: me } = useMe();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-52 shrink-0 bg-ledger-surface border-r border-ledger-rule flex flex-col transform transition-transform duration-200 md:relative md:translate-x-0 md:z-auto ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="px-5 py-4 border-b border-ledger-rule">
          <span className="text-sm font-sans font-semibold text-ledger-ink tracking-wide">ai_dash</span>
          <span className="ml-2 text-xs font-mono text-ledger-faint">v0.1</span>
        </div>
        {me?.username && (
          <div className="px-4 py-2.5 border-b border-ledger-rule flex items-center justify-between">
            <span className="text-xs font-mono text-ledger-dim truncate">{me.username}</span>
            <button
              type="button"
              onClick={() => logout()}
              className="text-xs font-sans text-ledger-faint hover:text-ledger-dim"
            >
              log out
            </button>
          </div>
        )}
        <nav className="flex-1 px-3 py-3 space-y-0.5">
          {nav.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              onClick={() => setMobileNavOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 pl-3 pr-3 py-2 border-l-2 text-sm font-sans transition-colors ${
                  isActive
                    ? "border-ledger-accent text-ledger-ink bg-ledger-raised"
                    : "border-transparent text-ledger-dim hover:text-ledger-ink hover:bg-ledger-raised/50"
                }`
              }
            >
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
        <header className="md:hidden sticky top-0 z-10 flex items-center gap-3 px-4 py-3 bg-ledger-surface border-b border-ledger-rule">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            className="text-ledger-dim text-xl leading-none"
          >
            ☰
          </button>
          <span className="text-sm font-sans font-semibold text-ledger-ink tracking-wide">ai_dash</span>
        </header>
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
