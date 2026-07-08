import { useEffect, useState } from "react";
import { useMe } from "../lib/api";

interface KeyEntry {
  key_prefix: string;
  user: string;
  created_at: string;
}

interface AccountEntry {
  username: string;
  is_admin: boolean;
  created_at: string;
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short", day: "numeric", year: "numeric",
  });
}

function UsersSection({ isBootstrap, isAdmin }: { isBootstrap: boolean; isAdmin: boolean }) {
  const [accounts, setAccounts] = useState<AccountEntry[]>([]);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [bootstrapped, setBootstrapped] = useState(false);

  useEffect(() => {
    if (isBootstrap) return;
    fetch("/api/accounts")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          setError(data.detail ?? "Failed to load accounts");
          return;
        }
        setAccounts(Array.isArray(data) ? data : []);
      })
      .catch(() => setError("Failed to load accounts"));
  }, [isBootstrap]);

  async function handleCreate() {
    const username = newUsername.trim();
    if (!username || !newPassword) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "Failed to create account");
        return;
      }
      if (isBootstrap) {
        setBootstrapped(true);
        return;
      }
      setAccounts((prev) => [...prev, data]);
      setNewUsername("");
      setNewPassword("");
    } catch {
      setError("Failed to create account");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(username: string) {
    if (!window.confirm(`Delete account for ${username}?`)) return;
    try {
      const res = await fetch(`/api/accounts/${username}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Failed to delete account");
        return;
      }
      setAccounts((prev) => prev.filter((a) => a.username !== username));
    } catch {
      setError("Failed to delete account");
    }
  }

  async function handleToggleAdmin(account: AccountEntry) {
    try {
      const res = await fetch(`/api/accounts/${account.username}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_admin: !account.is_admin }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail ?? "Failed to update account");
        return;
      }
      setAccounts((prev) => prev.map((a) => (a.username === account.username ? data : a)));
    } catch {
      setError("Failed to update account");
    }
  }

  if (isBootstrap && bootstrapped) {
    return (
      <section>
        <h2 className="text-xs font-sans text-ledger-faint uppercase tracking-wider mb-3">Users</h2>
        <div className="bg-ledger-raised border-l-2 border-provider-openai text-provider-openai font-mono text-xs p-3">
          Account created. The shared dashboard password no longer works — sign in with this
          account to continue.
        </div>
        <button
          onClick={() => { window.location.href = "/login"; }}
          className="mt-3 px-3 py-1.5 text-sm bg-ledger-raised text-ledger-ink hover:bg-ledger-rule transition-colors font-sans"
        >
          Go to login
        </button>
      </section>
    );
  }

  return (
    <section>
      <h2 className="text-xs font-sans text-ledger-faint uppercase tracking-wider mb-3">Users</h2>

      {(isBootstrap || isAdmin) && (
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            placeholder="username"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            className="flex-1 bg-ledger-raised border border-ledger-rule px-3 py-1.5 text-sm text-ledger-ink placeholder-ledger-faint font-mono focus:outline-none focus:border-ledger-faint"
          />
          <input
            type="password"
            placeholder="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="flex-1 bg-ledger-raised border border-ledger-rule px-3 py-1.5 text-sm text-ledger-ink placeholder-ledger-faint font-mono focus:outline-none focus:border-ledger-faint"
          />
          <button
            onClick={handleCreate}
            disabled={loading || !newUsername.trim() || !newPassword}
            className="px-3 py-1.5 text-sm bg-ledger-raised text-ledger-ink hover:bg-ledger-rule disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-sans"
          >
            Create Account
          </button>
        </div>
      )}

      {error && <p className="text-xs font-mono text-ledger-accent mb-3">{error}</p>}

      {!isBootstrap && (
        <div className="border border-ledger-rule overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-sm font-mono">
              <thead>
                <tr className="border-b border-ledger-rule text-xs font-sans text-ledger-faint uppercase tracking-wider">
                  <th className="px-4 py-2 text-left font-normal">Username</th>
                  <th className="px-4 py-2 text-left font-normal">Admin</th>
                  <th className="px-4 py-2 text-left font-normal">Created</th>
                  <th className="px-4 py-2 text-left font-normal"></th>
                </tr>
              </thead>
              <tbody>
                {accounts.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-4 text-center text-ledger-faint text-xs">No accounts yet</td>
                  </tr>
                ) : (
                  accounts.map((a) => (
                    <tr key={a.username} className="border-b border-ledger-rule/50 last:border-0 hover:bg-ledger-raised/60">
                      <td className="px-4 py-2.5 text-ledger-ink">{a.username}</td>
                      <td className="px-4 py-2.5">
                        <button
                          onClick={() => handleToggleAdmin(a)}
                          className={a.is_admin ? "text-provider-openai" : "text-ledger-faint hover:text-ledger-dim"}
                        >
                          {a.is_admin ? "admin" : "member"}
                        </button>
                      </td>
                      <td className="px-4 py-2.5 text-ledger-faint">{formatDate(a.created_at)}</td>
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={() => handleDelete(a.username)}
                          className="text-ledger-faint hover:text-ledger-accent transition-colors px-1"
                          title="Delete account"
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
        </div>
      )}
    </section>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<KeyEntry[]>([]);
  const [newUser, setNewUser] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch("/api/keys")
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          setError(data.detail ?? "Failed to load keys");
          return;
        }
        setKeys(Array.isArray(data) ? data : []);
      })
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

  return (
    <section>
      <h2 className="text-xs font-sans text-ledger-faint uppercase tracking-wider mb-3">API Keys</h2>

      <div className="flex gap-2 mb-4">
        <input
          type="text"
          placeholder="username"
          value={newUser}
          onChange={(e) => setNewUser(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
          className="flex-1 bg-ledger-raised border border-ledger-rule px-3 py-1.5 text-sm text-ledger-ink placeholder-ledger-faint font-mono focus:outline-none focus:border-ledger-faint"
        />
        <button
          onClick={handleCreate}
          disabled={loading || !newUser.trim()}
          className="px-3 py-1.5 text-sm bg-ledger-raised text-ledger-ink hover:bg-ledger-rule disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-sans"
        >
          Create Key
        </button>
      </div>

      {error && <p className="text-xs font-mono text-ledger-accent mb-3">{error}</p>}

      {createdKey && (
        <div className="mb-4 bg-ledger-raised border-l-2 border-provider-openai text-provider-openai font-mono text-xs p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="break-all">{createdKey}</span>
            <button
              onClick={() => navigator.clipboard.writeText(createdKey)}
              className="shrink-0 px-2 py-1 bg-ledger-rule hover:bg-ledger-rule/70 transition-colors"
            >
              Copy
            </button>
          </div>
          <p className="mt-2 opacity-70">This key is only shown once.</p>
        </div>
      )}

      <div className="border border-ledger-rule overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px] text-sm font-mono">
          <thead>
            <tr className="border-b border-ledger-rule text-xs font-sans text-ledger-faint uppercase tracking-wider">
              <th className="px-4 py-2 text-left font-normal">Key Prefix</th>
              <th className="px-4 py-2 text-left font-normal">User</th>
              <th className="px-4 py-2 text-left font-normal">Created</th>
              <th className="px-4 py-2 text-left font-normal"></th>
            </tr>
          </thead>
          <tbody>
            {keys.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-4 text-center text-ledger-faint text-xs">No keys yet</td>
              </tr>
            ) : (
              keys.map((k) => (
                <tr key={k.key_prefix} className="border-b border-ledger-rule/50 last:border-0 hover:bg-ledger-raised/60">
                  <td className="px-4 py-2.5 text-ledger-dim">{k.key_prefix}</td>
                  <td className="px-4 py-2.5 text-ledger-ink">{k.user}</td>
                  <td className="px-4 py-2.5 text-ledger-faint">{formatDate(k.created_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => handleDelete(k)}
                      className="text-ledger-faint hover:text-ledger-accent transition-colors px-1"
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
      </div>
    </section>
  );
}

export function Settings() {
  const { data: me } = useMe();
  const isBootstrap = !!me && me.username === null;
  const isAdmin = me?.is_admin ?? false;

  return (
    <div className="p-6 max-w-2xl space-y-8">
      <h1 className="text-lg font-sans font-semibold text-ledger-ink">Settings</h1>

      {(isBootstrap || isAdmin) && <UsersSection isBootstrap={isBootstrap} isAdmin={isAdmin} />}

      {isAdmin && <ApiKeysSection />}

      {!isBootstrap && !isAdmin && (
        <p className="text-sm font-sans text-ledger-faint">Only admins can manage users and API keys.</p>
      )}
    </div>
  );
}
