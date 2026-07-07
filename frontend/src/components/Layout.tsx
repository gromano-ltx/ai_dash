import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useMe, logout } from "../lib/api";

const nav = [
  { to: "/", label: "Overview", icon: "⬡" },
  { to: "/runs", label: "Runs", icon: "▶" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

export function Layout() {
  const { data: me } = useMe();
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
        {me?.username && (
          <div className="px-3 py-2.5 border-b border-slate-800 flex items-center justify-between">
            <span className="text-xs text-slate-400 font-mono truncate">{me.username}</span>
            <button
              type="button"
              onClick={() => logout()}
              className="text-xs text-slate-500 hover:text-slate-300 font-mono"
            >
              logout
            </button>
          </div>
        )}
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
