"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import Sidebar, { type ViewMode } from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import Breadcrumbs, { type Crumb } from "@/components/Breadcrumbs";
import FileGrid from "@/components/FileGrid";
import FileTable from "@/components/FileTable";
import PreviewModal from "@/components/PreviewModal";
import UploadDropzone from "@/components/UploadDropzone";
import type { DriveItem } from "@/components/FileCard";

interface DriveResponse {
  items: DriveItem[];
  breadcrumbs: Crumb[];
}

export default function DrivePage() {
  const [view, setView] = useState<ViewMode>("folder");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");
  const [folderId, setFolderId] = useState<string | null>(null);
  const [items, setItems] = useState<DriveItem[]>([]);
  const [breadcrumbs, setBreadcrumbs] = useState<Crumb[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewItem, setPreviewItem] = useState<DriveItem | null>(null);
  const [searchResults, setSearchResults] = useState<DriveItem[]>([]);
  const [liveConnected, setLiveConnected] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [stats, setStats] = useState({ total_files: 0, total_mb: 0 });
  const [sort] = useState<"name" | "size" | "date">("name");
  const [sortDir] = useState<"asc" | "desc">("asc");
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch stats from API
  const fetchStats = useCallback(async () => {
    try {
      const data = await fetch("/api/stats").then((r) => r.json());
      if (data && typeof data.total_files === "number") {
        setStats(data);
      }
    } catch {}
  }, []);

  // Fetch folder contents
  const fetchItems = useCallback(async () => {
    setLoading(true);
    setSelected(new Set());
    try {
      const params = new URLSearchParams({ view });
      if (view === "folder") {
        if (folderId) params.set("folder", folderId);
        params.set("sort", sort);
        params.set("dir", sortDir);
      }
      const data: DriveResponse = await fetch(`/api/drive?${params}`).then((r) => r.json());
      setItems(data.items || []);
      setBreadcrumbs(data.breadcrumbs || []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [view, folderId, sort, sortDir]);

  useEffect(() => {
    fetchItems();
    fetchStats();
  }, [fetchItems, fetchStats]);

  // SSE live sync
  useEffect(() => {
    const es = new EventSource("/api/events");
    es.onopen = () => setLiveConnected(true);
    es.onerror = () => setLiveConnected(false);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "update") {
          fetchItems();
          fetchStats();
        }
      } catch {}
    };
    return () => es.close();
  }, [fetchItems, fetchStats]);

  // Search
  const handleSearch = useCallback((q: string) => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!q || q.length < 2) { setSearchResults([]); return; }
    searchTimeout.current = setTimeout(async () => {
      try {
        const data = await fetch(`/api/search?q=${encodeURIComponent(q)}`).then((r) => r.json());
        setSearchResults(data.items ?? []);
      } catch {}
    }, 250);
  }, []);

  // Selection
  const handleSelect = useCallback((id: string, multi: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (multi) {
        if (next.has(id)) next.delete(id);
        else next.add(id);
      } else {
        next.clear();
        next.add(id);
      }
      return next;
    });
  }, []);

  // Open
  const handleOpen = useCallback((item: DriveItem) => {
    if (item.type === "folder") {
      setFolderId(item.id);
      setView("folder");
    } else {
      setPreviewItem(item);
    }
  }, []);

  // Preview navigation
  const handleNavigate = useCallback((dir: -1 | 1) => {
    const fileItems = items.filter((i) => i.type !== "folder");
    const idx = previewItem ? fileItems.findIndex((i) => i.id === previewItem.id) : -1;
    const next = fileItems[idx + dir];
    if (next) setPreviewItem(next);
  }, [items, previewItem]);

  // Actions
  const handleAction = useCallback(async (action: string, item: DriveItem) => {
    try {
      await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ids: [item.id] }),
      });
      fetchItems();
      fetchStats();
    } catch {}
  }, [fetchItems, fetchStats]);

  // Sync
  const handleSync = useCallback(async () => {
    setSyncing(true);
    try {
      await fetch(`/api/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start", target: "all" }),
      });
      await fetchItems();
      await fetchStats();
    } catch {}
    setSyncing(false);
  }, [fetchItems, fetchStats]);

  const fileItems = items.filter((i) => i.type !== "folder");

  return (
    <div className="flex h-screen bg-[#0d0f11] text-white overflow-hidden">
      <Sidebar
        view={view}
        onView={(v) => { setView(v); if (v === "folder") setFolderId(null); }}
        folderId={folderId}
        totalFiles={stats.total_files || items.length}
        totalMb={stats.total_mb}
        syncing={syncing}
        onSync={handleSync}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar
          viewMode={viewMode}
          onViewMode={setViewMode}
          liveConnected={liveConnected}
          onSearch={handleSearch}
          searchResults={searchResults}
          onSearchOpen={handleOpen}
        />

        {/* Toolbar */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/[0.04] shrink-0">
          <div className="flex items-center gap-3">
            <Breadcrumbs crumbs={breadcrumbs} onNavigate={(id) => { setFolderId(id); setView("folder"); }} />
            {selected.size > 0 && (
              <span className="text-xs bg-blue-500/20 text-blue-300 px-2.5 py-1 rounded-lg">
                {selected.size} selected
              </span>
            )}
          </div>
          <UploadDropzone folderId={folderId} onUploadComplete={() => { fetchItems(); fetchStats(); }} />
        </div>

        {/* File area */}
        <div className="flex-1 overflow-y-auto">
          {viewMode === "grid" ? (
            <FileGrid
              items={items}
              selected={selected}
              onSelect={handleSelect}
              onOpen={handleOpen}
              onPreview={setPreviewItem}
              onAction={handleAction}
              loading={loading}
            />
          ) : (
            <FileTable
              items={items}
              selected={selected}
              onSelect={handleSelect}
              onOpen={handleOpen}
              onPreview={setPreviewItem}
            />
          )}
        </div>
      </div>

      {/* Preview modal */}
      <PreviewModal
        item={previewItem}
        items={fileItems}
        onClose={() => setPreviewItem(null)}
        onNavigate={handleNavigate}
      />
    </div>
  );
}
