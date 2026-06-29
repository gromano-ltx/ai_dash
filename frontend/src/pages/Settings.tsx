import { useEffect, useState } from "react";

interface KeyEntry {
  key_prefix: string;
  user: string;
  created_at: string;
}

export function Settings() {
  const [keys, setKeys] = useState<KeyEntry[]>([]);
  const [newUser, setNewUser] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch("/api/keys")
      .then((r) => r.json())
      .then(setKeys)
      .catch(() => setError("Failed to load keys"));
  }, []);

  async function handleCreate() {
    const user = newUser.trim();
    if (!user) return;
    setLoading(true);
    setError(null);
    setCreatedKey(null);
    try {
      const res = await fetch("/api/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user }),
      });
      if (!res.ok) {
        const data = await res.json();
        setError(data.detail ?? "Failed to create key");
        return;
      }
      const data = await res.json();
      setCreatedKey(data.key);
      setKeys((prev) => [
        { key_prefix: data.key.slice(0, 12) + "…", user: data.user, created_at: data.created_at },
        ...prev,
      ]);
      setNewUser("");
    } catch {
      setError("Failed to create key");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(entry: KeyEntry) {
    if (!window.confirm(`Delete key for ${entry.user}?`)) return;
    const prefix = entry.key_prefix.replace("…", "");
    try {
      const res = await fetch(`/api/keys/${prefix}`, { method: "DELETE" });
      if (!res.ok) {
        setError("Failed to delete key");
        return;
      }
      setKeys((prev) => prev.filter((k) => k.key_prefix !== entry.key_prefix));
      if (createdKey && createdKey.startsWith(prefix)) setCreatedKey(null);
    } catch {
      setError("Failed to delete key");
    }
  }

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
    });
  }

  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-lg font-mono font-semibold text-slate-100 mb-6">Settings</h1>

      <section>
        <h2 className="text-xs text-slate-500 font-mono uppercase tracking-wider mb-3">API Keys</h2>

        <div className="flex gap-2 mb-4">
          <input
            type="text"
            placeholder="username"
            value={newUser}
            onChange={(e) => setNewUser(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="flex-1 bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 font-mono focus:outline-none focus:border-slate-500"
          />
          <button
            onClick={handleCreate}
            disabled={loading || !newUser.trim()}
            className="px-3 py-1.5 rounded text-sm bg-slate-700 text-slate-200 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-mono"
          >
            Create Key
          </button>
        </div>

        {error && <p className="text-xs text-red-400 mb-3">{error}</p>}

        {createdKey && (
          <div className="mb-4 bg-slate-800 border border-emerald-500/30 text-emerald-300 font-mono text-xs p-3 rounded">
            <div className="flex items-center justify-between gap-3">
              <span className="break-all">{createdKey}</span>
              <button
                onClick={() => navigator.clipboard.writeText(createdKey)}
                className="shrink-0 px-2 py-1 rounded bg-emerald-900/40 hover:bg-emerald-900/70 transition-colors"
              >
                Copy
              </button>
            </div>
            <p className="mt-2 text-emerald-500/70">This key is only shown once.</p>
          </div>
        )}

        <div className="border border-slate-800 rounded overflow-hidden">
          <table className="w-full text-sm font-mono">
            <thead>
              <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-4 py-2 text-left font-normal">Key Prefix</th>
                <th className="px-4 py-2 text-left font-normal">User</th>
                <th className="px-4 py-2 text-left font-normal">Created</th>
                <th className="px-4 py-2 text-left font-normal"></th>
              </tr>
            </thead>
            <tbody>
              {keys.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-4 py-4 text-center text-slate-600 text-xs">No keys yet</td>
                </tr>
              ) : (
                keys.map((k) => (
                  <tr key={k.key_prefix} className="border-b border-slate-800/50 last:border-0 hover:bg-slate-800/30">
                    <td className="px-4 py-2.5 text-slate-400">{k.key_prefix}</td>
                    <td className="px-4 py-2.5 text-slate-300">{k.user}</td>
                    <td className="px-4 py-2.5 text-slate-500">{formatDate(k.created_at)}</td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        onClick={() => handleDelete(k)}
                        className="text-slate-600 hover:text-red-400 transition-colors px-1"
                        title="Delete key"
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
