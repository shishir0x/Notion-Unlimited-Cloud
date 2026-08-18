"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { Search, Grid, List, Wifi, WifiOff, X } from "lucide-react";
import type { DriveItem } from "./FileCard";

interface TopBarProps {
  viewMode: "grid" | "list";
  onViewMode: (v: "grid" | "list") => void;
  liveConnected: boolean;
  onSearch: (q: string) => void;
  searchResults: DriveItem[];
  onSearchOpen: (item: DriveItem) => void;
}

export default function TopBar({ viewMode, onViewMode, liveConnected, onSearch, searchResults, onSearchOpen }: TopBarProps) {
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setQuery(v);
    onSearch(v);
  }, [onSearch]);

  const clear = useCallback(() => {
    setQuery("");
    onSearch("");
    inputRef.current?.focus();
  }, [onSearch]);

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
    <div className="flex items-center gap-4 px-5 py-3 border-b border-white/5 bg-[#0f1012] shrink-0">
      {/* Search */}
      <div className="flex-1 relative" ref={dropRef}>
        <div className={`flex items-center gap-2.5 bg-white/[0.05] border rounded-xl px-3.5 py-2.5 transition-all ${focused ? "border-blue-500/50 ring-1 ring-blue-500/20" : "border-white/[0.07]"}`}>
          <Search size={14} className="text-gray-500 shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={handleChange}
            onFocus={() => setFocused(true)}
            placeholder="Search in My Drive..."
            className="flex-1 bg-transparent text-sm text-white placeholder-gray-600 outline-none"
          />
          {query && (
            <button onClick={clear} className="text-gray-500 hover:text-white transition-colors">
              <X size={13} />
            </button>
          )}
        </div>

        {/* Search dropdown */}
        {showDrop && (
          <div className="absolute top-full mt-2 left-0 right-0 bg-[#1a1c1f] border border-white/10 rounded-xl shadow-2xl z-50 overflow-hidden">
            {searchResults.length === 0 ? (
              <p className="px-4 py-3 text-gray-500 text-sm">No results for "{query}"</p>
            ) : (
              <div className="divide-y divide-white/[0.04] max-h-72 overflow-y-auto">
                {searchResults.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => { onSearchOpen(item); setFocused(false); setQuery(""); onSearch(""); }}
                    className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-white/[0.04] text-left transition-colors"
                  >
                    <span className="text-xs bg-white/[0.06] text-gray-400 rounded px-1.5 py-0.5 uppercase font-mono shrink-0">
                      {item.extension.replace(".", "") || "DIR"}
                    </span>
                    <span className="text-sm text-white/80 truncate">{item.name}</span>
                    <span className="text-xs text-gray-600 ml-auto shrink-0">{item.sizeMb.toFixed(1)} MB</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* View toggle */}
      <div className="flex items-center bg-white/[0.05] border border-white/[0.07] rounded-lg p-1">
        <button
          onClick={() => onViewMode("grid")}
          className={`p-1.5 rounded-md transition-colors ${viewMode === "grid" ? "bg-white/10 text-white" : "text-gray-500 hover:text-white"}`}
        >
          <Grid size={14} />
        </button>
        <button
          onClick={() => onViewMode("list")}
          className={`p-1.5 rounded-md transition-colors ${viewMode === "list" ? "bg-white/10 text-white" : "text-gray-500 hover:text-white"}`}
        >
          <List size={14} />
        </button>
      </div>

      {/* Live status */}
      <div className={`flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border ${
        liveConnected ? "text-green-400 border-green-500/20 bg-green-500/5" : "text-gray-500 border-white/5"
      }`}>
        {liveConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
        <span className="hidden sm:inline">{liveConnected ? "Live" : "Offline"}</span>
      </div>
    </div>
  );
}
