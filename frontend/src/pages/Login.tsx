import { useState, type FormEvent } from "react";
import { login } from "../lib/api";

export function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      window.location.href = "/";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-ledger-bg">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-xs bg-ledger-surface border-t-2 border-ledger-accent p-6 space-y-4"
      >
        <div>
          <h1 className="text-sm font-sans font-semibold text-ledger-ink tracking-wide">ai_dash</h1>
          <p className="text-xs font-mono text-ledger-faint mt-1">Sign in to continue</p>
        </div>
        <div className="space-y-1">
          <label className="text-xs font-mono text-ledger-faint block">username</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            className="w-full bg-ledger-raised border border-ledger-rule px-3 py-1.5 text-sm text-ledger-ink font-mono focus:outline-none focus:border-ledger-faint"
          />
        </div>
        <div className="space-y-1">
          <label className="text-xs font-mono text-ledger-faint block">password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full bg-ledger-raised border border-ledger-rule px-3 py-1.5 text-sm text-ledger-ink font-mono focus:outline-none focus:border-ledger-faint"
          />
        </div>
        {error && <p className="text-xs font-mono text-ledger-accent">{error}</p>}
        <button
          type="submit"
          disabled={loading || !username.trim() || !password}
          className="w-full px-3 py-1.5 text-sm bg-ledger-raised text-ledger-ink hover:bg-ledger-rule disabled:opacity-40 disabled:cursor-not-allowed transition-colors font-sans"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
