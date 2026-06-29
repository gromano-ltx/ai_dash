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

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="w-52 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col">
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
      <main className="flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
