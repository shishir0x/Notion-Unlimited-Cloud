"use client";
import { useMemo } from "react";
import { HardDrive, Clock, Star, Trash2, RefreshCw, ChevronRight, Infinity } from "lucide-react";

export type ViewMode = "folder" | "recent" | "starred" | "trash";

interface SidebarProps {
  view: ViewMode;
  onView: (v: ViewMode) => void;
  folderId: string | null;
  totalFiles: number;
  totalMb: number;
  syncing: boolean;
  onSync: () => void;
}

const navItems = [
  { id: "folder" as ViewMode, label: "My Drive", icon: HardDrive },
  { id: "recent" as ViewMode, label: "Recent", icon: Clock },
  { id: "starred" as ViewMode, label: "Starred", icon: Star },
  { id: "trash" as ViewMode, label: "Trash", icon: Trash2 },
];

export default function Sidebar({ view, onView, totalFiles, totalMb, syncing, onSync }: SidebarProps) {
  const gb = useMemo(() => (totalMb / 1024).toFixed(2), [totalMb]);

  return (
    <aside className="w-64 shrink-0 bg-[#111315] border-r border-white/5 flex flex-col h-full">
      {/* Logo */}
      <div className="px-5 py-5 flex items-center gap-3 border-b border-white/5">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
          <HardDrive size={15} className="text-white" />
        </div>
        <span className="text-white font-semibold text-[15px] tracking-tight">NotionDrive</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => onView(id)}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150 group ${
              view === id
                ? "bg-white/10 text-white"
                : "text-gray-400 hover:bg-white/5 hover:text-white"
            }`}
          >
            <Icon size={16} className={view === id ? "text-blue-400" : "text-gray-500 group-hover:text-gray-300"} />
            {label}
            {view === id && <ChevronRight size={14} className="ml-auto text-gray-500" />}
          </button>
        ))}
      </nav>

      {/* Storage footer */}
      <div className="px-4 py-4 border-t border-white/5 space-y-3">
        <button
          onClick={onSync}
          disabled={syncing}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs text-gray-400 hover:bg-white/5 hover:text-white transition-all"
        >
          <RefreshCw size={13} className={syncing ? "animate-spin text-blue-400" : ""} />
          {syncing ? "Syncing..." : "Sync Now"}
        </button>

        <div className="bg-white/[0.04] rounded-xl p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-gray-400 font-medium">Storage</span>
            <span className="text-[11px] text-blue-400 font-semibold flex items-center gap-1">
              <Infinity size={11} /> Unlimited
            </span>
          </div>
          <div className="h-1.5 bg-white/10 rounded-full overflow-hidden">
            <div className="h-full w-1/3 bg-gradient-to-r from-blue-500 to-indigo-500 rounded-full" />
          </div>
          <p className="text-[11px] text-gray-500">
            {gb} GB · {totalFiles.toLocaleString()} files
          </p>
        </div>
      </div>
    </aside>
  );
}
