"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { Search, Grid, List, Wifi, WifiOff, X, Menu } from "lucide-react";
import type { DriveItem } from "@/lib/types";

interface TopBarProps {
  viewMode: "grid" | "list";
  onViewMode: (v: "grid" | "list") => void;
  liveConnected: boolean;
  searchRef: React.RefObject<HTMLInputElement | null>;
  onToggleSidebar: () => void;
  onSearch: (q: string) => void;
  searchResults: DriveItem[];
  onSearchOpen: (item: DriveItem) => void;
}

export default function TopBar({
  viewMode, onViewMode, liveConnected, searchRef, onToggleSidebar,
  onSearch, searchResults, onSearchOpen,
}: TopBarProps) {
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setQuery(v);
    onSearch(v);
  }, [onSearch]);

  const clear = useCallback(() => {
    setQuery("");
    onSearch("");
    searchRef.current?.focus();
  }, [onSearch, searchRef]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) {
        setFocused(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const showDrop = focused && query.length >= 2;

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-elevated)] shrink-0">
      {/* Mobile sidebar toggle */}
      <button
        onClick={onToggleSidebar}
        className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] md:hidden shrink-0"
        aria-label="Toggle sidebar"
      >
        <Menu size={18} />
      </button>

      {/* Search */}
      <div className="flex-1 relative" ref={dropRef}>
        <div className={`flex items-center gap-2.5 bg-[var(--bg-soft)] border rounded-xl px-3.5 py-2 transition-all ${
          focused ? "border-blue-500/50 ring-1 ring-blue-500/20" : "border-transparent"
        }`}>
          <Search size={14} className="text-[var(--text-muted)] shrink-0" />
          <input
            ref={searchRef}
            value={query}
            onChange={handleChange}
            onFocus={() => setFocused(true)}
            placeholder="Search in My Drive...  (Ctrl+K)"
            className="flex-1 bg-transparent text-sm text-[var(--text)] placeholder-[var(--text-muted)] outline-none"
          />
          {query && (
            <button onClick={clear} className="text-[var(--text-muted)] hover:text-[var(--text)] transition-colors" aria-label="Clear search">
              <X size={13} />
            </button>
          )}
        </div>

        {/* Search dropdown */}
        {showDrop && (
          <div className="absolute top-full mt-2 left-0 right-0 bg-[var(--bg-elevated)] border border-[var(--border)] rounded-xl shadow-2xl z-50 overflow-hidden">
            {searchResults.length === 0 ? (
              <p className="px-4 py-3 text-[var(--text-muted)] text-sm">No results for &quot;{query}&quot;</p>
            ) : (
              <div className="divide-y divide-[var(--border)] max-h-72 overflow-y-auto">
                {searchResults.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => { onSearchOpen(item); setFocused(false); setQuery(""); onSearch(""); }}
                    className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--bg-hover)] text-left transition-colors"
                  >
                    <span className="text-xs bg-[var(--bg-soft)] text-[var(--text-secondary)] rounded px-1.5 py-0.5 uppercase font-mono shrink-0">
                      {item.extension.replace(".", "") || "DIR"}
                    </span>
                    <span className="text-sm text-[var(--text)] truncate">{item.name}</span>
                    <span className="text-xs text-[var(--text-muted)] ml-auto shrink-0">{item.sizeMb.toFixed(1)} MB</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* View toggle */}
      <div className="flex items-center bg-[var(--bg-soft)] rounded-lg p-1 shrink-0">
        <button
          onClick={() => onViewMode("grid")}
          className={`p-1.5 rounded-md transition-colors ${viewMode === "grid" ? "bg-[var(--bg-hover)] text-[var(--text)]" : "text-[var(--text-muted)] hover:text-[var(--text)]"}`}
          aria-label="Grid view"
        >
          <Grid size={14} />
        </button>
        <button
          onClick={() => onViewMode("list")}
          className={`p-1.5 rounded-md transition-colors ${viewMode === "list" ? "bg-[var(--bg-hover)] text-[var(--text)]" : "text-[var(--text-muted)] hover:text-[var(--text)]"}`}
          aria-label="List view"
        >
          <List size={14} />
        </button>
      </div>

      {/* Live status */}
      <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border shrink-0 ${
        liveConnected ? "text-green-500 border-green-500/20 bg-green-500/10" : "text-[var(--text-muted)] border-[var(--border)]"
      }`} title={liveConnected ? "Connected to storage service" : "Storage service offline"}>
        {liveConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
        <span className="hidden sm:inline">{liveConnected ? "Live" : "Offline"}</span>
      </div>
    </div>
  );
}
