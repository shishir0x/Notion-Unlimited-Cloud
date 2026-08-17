"use client";

import { useState } from "react";
import { HardDrive, Lock, AlertCircle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

interface LoginGateProps {
  protectedRequired: boolean;
  onAuthenticated: () => void;
}

export default function LoginGate({ protectedRequired, onAuthenticated }: LoginGateProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await api.login(password);
      if (res.success) {
        onAuthenticated();
      } else {
        setError(res.error ?? "Login failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the storage service");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[var(--bg)] p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/20 mb-4">
            <HardDrive size={28} className="text-white" />
          </div>
          <h1 className="text-2xl font-semibold text-[var(--text)] tracking-tight">NotionDrive</h1>
          <p className="text-sm text-[var(--text-muted)] mt-1.5">Unlimited cloud storage</p>
        </div>

        <form
          onSubmit={submit}
          className="bg-[var(--bg-elevated)] border border-[var(--border)] rounded-2xl p-6 shadow-xl space-y-4"
        >
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Lock size={14} className="text-[var(--text-muted)]" />
            {protectedRequired ? "This drive is protected. Enter the password to continue." : "Sign in to continue."}
          </div>

          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            autoFocus
            className="w-full px-3.5 py-2.5 bg-[var(--bg-inset)] border border-[var(--border)] rounded-xl text-sm text-[var(--text)] placeholder-[var(--text-muted)] outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]"
          />

          {error && (
            <p className="flex items-center gap-1.5 text-xs text-red-400">
              <AlertCircle size={13} /> {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white rounded-xl text-sm font-semibold transition-colors"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Lock size={14} />}
            Unlock drive
          </button>
        </form>
      </div>
    </div>
  );
}
