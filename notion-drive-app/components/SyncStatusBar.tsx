"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import type { SyncState } from "@/lib/types";

export default function SyncStatusBar() {
  const [state, setState] = useState<SyncState | null>(null);
  const [lastChecked, setLastChecked] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const s = await api.syncStatus();
        if (!cancelled) {
          setState(s);
          setLastChecked(Date.now());
        }
      } catch {
        // backend unreachable — leave last state
      }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!state) return null;

  const running = Boolean(state.is_running);
  const percent = Math.min(100, Math.max(0, Number(state.percent) || 0));

  return (
    <div className="shrink-0 border-t border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-2 flex items-center gap-3 text-xs">
      {running ? (
        <>
          <Loader2 size={13} className="text-blue-400 animate-spin shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[var(--text-secondary)] truncate">
                Syncing <span className="text-[var(--text)] font-medium">{state.current_file || "…"}</span>
                {state.current_target ? ` · ${state.current_target}` : ""}
              </span>
              <span className="text-[var(--text-muted)] shrink-0">
                {state.synced_files}/{state.total_files} · {percent}%
              </span>
            </div>
            <div className="mt-1 h-1 bg-[var(--bg-soft)] rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${percent}%` }}
              />
            </div>
            <p className="mt-0.5 text-[11px] text-[var(--text-muted)] truncate">
              {state.speed_str} {state.current_size_str ? `· ${state.current_size_str}` : ""}
            </p>
          </div>
        </>
      ) : (
        <>
          <CheckCircle2 size={13} className="text-green-400 shrink-0" />
          <span className="text-[var(--text-secondary)]">
            <span className="text-green-400 font-medium">● Synced</span>
            <span className="text-[var(--text-muted)]">
              {" "}· last checked {formatRelative(lastChecked)}
            </span>
          </span>
          {state.status_message && (
            <span className="text-[var(--text-muted)] truncate flex-1 text-right hidden md:inline">
              {state.status_message}
            </span>
          )}
        </>
      )}
    </div>
  );
}

export function SyncNowButton({ onSync, syncing }: { onSync: () => void; syncing: boolean }) {
  return (
    <button
      onClick={onSync}
      disabled={syncing}
      className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-[var(--text-muted)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)] transition-all disabled:opacity-60"
    >
      <RefreshCw size={13} className={syncing ? "animate-spin text-blue-400" : ""} />
      {syncing ? "Syncing..." : "Sync Now"}
    </button>
  );
}
