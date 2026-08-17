"use client";
import { useMemo } from "react";
import { HardDrive, Clock, Star, Trash2, RefreshCw, ChevronRight, Infinity, X } from "lucide-react";
import type { ViewMode } from "@/lib/types";

interface SidebarProps {
  view: ViewMode;
  onView: (v: ViewMode) => void;
  totalFiles: number;
  totalMb: number;
  syncing: boolean;
  onSync: () => void;
  onClose?: () => void;
}

const navItems: { id: ViewMode; label: string; icon: typeof HardDrive }[] = [
  { id: "folder", label: "My Drive", icon: HardDrive },
  { id: "recent", label: "Recent", icon: Clock },
  { id: "starred", label: "Starred", icon: Star },
  { id: "trash", label: "Trash", icon: Trash2 },
];

export default function Sidebar({ view, onView, totalFiles, totalMb, syncing, onSync, onClose }: SidebarProps) {
  const gb = useMemo(() => (totalMb / 1024).toFixed(2), [totalMb]);

  return (
    <aside className="w-64 shrink-0 bg-[var(--bg-elevated)] border-r border-[var(--border)] flex flex-col h-full">
      {/* Logo */}
      <div className="px-5 py-5 flex items-center gap-3 border-b border-[var(--border)]">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
          <HardDrive size={15} className="text-white" />
        </div>
        <span className="text-[var(--text)] font-semibold text-[15px] tracking-tight flex-1">NotionDrive</span>
        {onClose && (
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] md:hidden" aria-label="Close sidebar">
            <X size={15} />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onView(id)}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150 group ${
              view === id
                ? "bg-[var(--bg-hover)] text-[var(--text)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--bg-soft)] hover:text-[var(--text)]"
            }`}
          >
            <Icon size={16} className={view === id ? "text-blue-500" : "text-[var(--text-muted)] group-hover:text-[var(--text-secondary)]"} />
            {label}
            {view === id && <ChevronRight size={14} className="ml-auto text-[var(--text-muted)]" />}
          </button>
        ))}
      </nav>

      {/* Storage footer */}
      <div className="px-4 py-4 border-t border-[var(--border)] space-y-3">
        <button
          onClick={onSync}
          disabled={syncing}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)] transition-all disabled:opacity-60"
        >
          <RefreshCw size={13} className={syncing ? "animate-spin text-blue-500" : ""} />
          {syncing ? "Syncing..." : "Sync Now"}
        </button>

        <div className="bg-[var(--bg-soft)] rounded-xl p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-[var(--text-secondary)] font-medium">Storage</span>
            <span className="text-[11px] text-blue-500 font-semibold flex items-center gap-1">
              <Infinity size={11} /> Unlimited
            </span>
          </div>
          <div className="h-1.5 bg-[var(--border)] rounded-full overflow-hidden">
            <div className="h-full w-1/3 bg-gradient-to-r from-blue-500 to-indigo-500 rounded-full" />
          </div>
          <p className="text-[11px] text-[var(--text-muted)]">
            {gb} GB · {totalFiles.toLocaleString()} files
          </p>
        </div>
      </div>
    </aside>
  );
}
