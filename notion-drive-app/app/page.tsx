"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  Download, Eye, FolderInput, FolderPlus, Info, Pencil, Plus, RefreshCw, Star, Trash2, Upload,
} from "lucide-react";

import { api, downloadUrl, folderZipUrl } from "@/lib/api";
import { ApiError, type AuthStatus, type Breadcrumb, type DriveItem, type SortKey, type ViewMode } from "@/lib/types";

import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import Breadcrumbs from "@/components/Breadcrumbs";
import FileGrid from "@/components/FileGrid";
import FileTable from "@/components/FileTable";
import PreviewModal from "@/components/PreviewModal";
import LoginGate from "@/components/LoginGate";
import SelectionToolbar from "@/components/SelectionToolbar";
import ContextMenu, { type MenuItem } from "@/components/ContextMenu";
import DetailsPanel from "@/components/DetailsPanel";
import SyncStatusBar from "@/components/SyncStatusBar";
import UploadManager from "@/components/UploadManager";
import NameDialog from "@/components/NameDialog";
import MoveDialog from "@/components/MoveDialog";
import { EmptyFolder, EmptyResults, ErrorState, OfflineState } from "@/components/EmptyState";
import { useUploads } from "@/hooks/useUploads";

// ── URL routing helpers ────────────────────────────────────────────────────
function parsePath(pathname: string): { view: ViewMode; folderId: string | null } {
  let segments = pathname.split("/").filter(Boolean);
  if (segments[0] === "drive") segments = segments.slice(1);
  if (segments[0] === "folder") return { view: "folder", folderId: segments[1] ?? null };
  if (segments[0] === "recent") return { view: "recent", folderId: null };
  if (segments[0] === "starred") return { view: "starred", folderId: null };
  if (segments[0] === "trash") return { view: "trash", folderId: null };
  return { view: "folder", folderId: null };
}

function pathFor(view: ViewMode, folderId: string | null): string {
  switch (view) {
    case "folder":
      return folderId ? `/folder/${folderId}` : "/";
    case "recent":
      return "/recent";
    case "starred":
      return "/starred";
    case "trash":
      return "/trash";
  }
}

const SORT_MAP: Record<string, string> = {
  name: "name",
  size: "size_mb",
  mtime: "mtime",
  type: "type",
};

export default function DrivePage() {
  const pathname = usePathname();
  const router = useRouter();
  const route = useMemo(() => parsePath(pathname ?? "/"), [pathname]);
  const view = route.view;
  const folderId = route.folderId;

  // ── UI state ──────────────────────────────────────────────────────────────
  const [viewMode, setViewModeState] = useState<"grid" | "list">(() => {
    if (typeof window === "undefined") return "grid";
    return (localStorage.getItem("nd-view") as "grid" | "list") || "grid";
  });
  const [sortKey, setSortKeyState] = useState<SortKey>(() => {
    if (typeof window === "undefined") return "name";
    const v = localStorage.getItem("nd-sort");
    return v === "size" || v === "mtime" || v === "type" ? v : "name";
  });
  const [sortDir, setSortDirState] = useState<"asc" | "desc">(() => {
    if (typeof window === "undefined") return "asc";
    return (localStorage.getItem("nd-dir") as "asc" | "desc") || "asc";
  });

  const [items, setItems] = useState<DriveItem[]>([]);
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [searchResults, setSearchResults] = useState<DriveItem[]>([]);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewItem, setPreviewItem] = useState<DriveItem | null>(null);
  const [detailsItem, setDetailsItem] = useState<DriveItem | null>(null);
  const [menu, setMenu] = useState<{ x: number; y: number; item: DriveItem | null } | null>(null);
  const [renameItem, setRenameItem] = useState<DriveItem | null>(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [newMenuOpen, setNewMenuOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [uploadPanelOpen, setUploadPanelOpen] = useState(false);
  const [stats, setStats] = useState({ total_files: 0, total_mb: 0 });
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [liveConnected, setLiveConnected] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [draggingOver, setDraggingOver] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const [refreshCount, setRefreshCount] = useState(0);
  const lastSelectedRef = useRef<string | null>(null);
  const selectOnLoadRef = useRef<string | null>(null);
  const dragItemIdRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const uploads = useUploads();

  // ── Persisted settings ────────────────────────────────────────────────────
  const setViewMode = (v: "grid" | "list") => {
    setViewModeState(v);
    localStorage.setItem("nd-view", v);
  };
  const setSortKey = (k: SortKey) => {
    setSortKeyState(k);
    localStorage.setItem("nd-sort", k);
  };
  const setSortDir = (d: "asc" | "desc") => {
    setSortDirState(d);
    localStorage.setItem("nd-dir", d);
  };

  // ── Auth bootstrap ────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    api.authStatus()
      .then((s) => { if (!cancelled) setAuth(s); })
      .catch(() => { if (!cancelled) setOffline(true); });
    return () => { cancelled = true; };
  }, []);

  // ── Data loading ──────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      let data;
      if (view === "folder") data = await api.drive(folderId, SORT_MAP[sortKey] ?? "name", sortDir);
      else if (view === "recent") data = await api.recent();
      else if (view === "starred") data = await api.starred();
      else data = await api.trash();
      setItems(data.items);
      setBreadcrumbs(data.breadcrumbs);
      if (selectOnLoadRef.current) {
        const sel = data.items.find((i) => i.id === selectOnLoadRef.current);
        if (sel) setSelected(new Set([sel.id]));
        selectOnLoadRef.current = null;
      } else {
        setSelected(new Set());
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setAuth({ protected: true, authenticated: false });
      } else {
        setLoadError(err instanceof Error ? err.message : "Failed to load");
      }
    } finally {
      setLoading(false);
    }
  }, [view, folderId, sortKey, sortDir]);

  useEffect(() => {
    // Data fetch on mount / route change — the setState calls happen inside
    // the async loadData, which is the accepted pattern for effects.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadData();
  }, [loadData, refreshCount]);

  // ── Stats ─────────────────────────────────────────────────────────────────
  const loadStats = useCallback(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);
  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const bump = useCallback(() => {
    setRefreshCount((c) => c + 1);
    void loadData();
    void loadStats();
  }, [loadData, loadStats]);

  // ── Live updates (SSE) ────────────────────────────────────────────────────
  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource("/api/sync/events");
    } catch {
      // EventSource failed to construct — stay disconnected.
      return;
    }
    es.onopen = () => setLiveConnected(true);
    es.onerror = () => setLiveConnected(false);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as { type?: string };
        if (data.type && data.type !== "connected") {
          void loadData();
          void loadStats();
        }
      } catch { /* ignore malformed */ }
    };
    return () => es.close();
  }, [loadData, loadStats]);

  // ── Actions ───────────────────────────────────────────────────────────────
  const runAction = useCallback(async (action: string, ids: string[], payload?: Record<string, unknown>) => {
    try {
      const res = await api.action(action, ids, payload);
      if (!res.success) throw new Error(res.error ?? "Action failed");
      return true;
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Action failed");
      return false;
    }
  }, []);

  const afterMutate = useCallback(() => {
    setSelected(new Set());
    void bump();
  }, [bump]);

  const doDelete = useCallback(
    async (ids: Set<string>) => {
      const arr = Array.from(ids);
      if (arr.length === 0) return;
      const msg = view === "trash"
        ? `Permanently delete ${arr.length} item(s)? This cannot be undone.`
        : `Move ${arr.length} item(s) to trash?`;
      if (!window.confirm(msg)) return;
      const action = view === "trash" ? "delete-permanent" : "delete";
      const ok = await runAction(action, arr);
      if (ok) afterMutate();
    },
    [view, runAction, afterMutate],
  );

  const toggleStar = useCallback(
    async (item: DriveItem) => {
      const ok = await runAction(item.starred ? "unstar" : "star", [item.id]);
      if (ok) {
        setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, starred: !i.starred } : i)));
        if (view === "starred" && !item.starred) void bump();
      }
    },
    [runAction, view, bump],
  );

  // ── Selection ─────────────────────────────────────────────────────────────
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
      lastSelectedRef.current = id;
      return next;
    });
  }, []);

  const handleSelectRange = useCallback((id: string) => {
    const anchor = lastSelectedRef.current;
    if (!anchor) {
      handleSelect(id, false);
      return;
    }
    const ids = items.map((i) => i.id);
    const a = ids.indexOf(anchor);
    const b = ids.indexOf(id);
    if (a === -1 || b === -1) {
      handleSelect(id, false);
      return;
    }
    const [start, end] = a < b ? [a, b] : [b, a];
    setSelected(new Set(ids.slice(start, end + 1)));
  }, [items, handleSelect]);

  // ── Navigation ────────────────────────────────────────────────────────────
  const navigate = useCallback((v: ViewMode, id: string | null) => {
    setMenu(null);
    setDetailsItem(null);
    setPreviewItem(null);
    router.push(pathFor(v, id));
  }, [router]);

  const openItem = useCallback((item: DriveItem) => {
    if (item.type === "folder") {
      navigate("folder", item.id);
    } else {
      setPreviewItem(item);
    }
  }, [navigate]);

  // ── Downloads ─────────────────────────────────────────────────────────────
  const downloadItems = useCallback((itemsToDownload: DriveItem[]) => {
    for (const item of itemsToDownload) {
      if (item.type === "folder") {
        window.open(folderZipUrl(item), "_blank");
      } else {
        const a = document.createElement("a");
        a.href = downloadUrl(item.id, item.localPath, true);
        a.download = item.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    }
  }, []);

  // ── Uploads / drop ────────────────────────────────────────────────────────
  const onDropFiles = useCallback(
    (files: FileList | File[]) => {
      uploads.addFiles(files, folderId);
      setUploadPanelOpen(true);
    },
    [uploads, folderId],
  );

  // ── Context menus ─────────────────────────────────────────────────────────
  const itemMenuItems = useCallback(
    (item: DriveItem): MenuItem[] => {
      if (view === "trash") {
        return [
          { label: "Restore", icon: RefreshCw, onClick: () => void runAction("restore", [item.id]).then((ok) => { if (ok) afterMutate(); }) },
          { label: "Delete permanently", icon: Trash2, danger: true, onClick: () => void doDelete(new Set([item.id])) },
        ];
      }
      return [
        { label: "Open", icon: FolderPlus, onClick: () => openItem(item) },
        { label: "Preview", icon: Eye, onClick: () => setPreviewItem(item) },
        { label: "Download", icon: Download, onClick: () => downloadItems([item]) },
        { separator: true },
        { label: "Rename", icon: Pencil, onClick: () => setRenameItem(item) },
        { label: "Move to", icon: FolderInput, onClick: () => { setSelected(new Set([item.id])); setMoveOpen(true); } },
        { label: item.starred ? "Unstar" : "Star", icon: Star, onClick: () => void toggleStar(item) },
        { label: "Get info", icon: Info, onClick: () => setDetailsItem(item) },
        { separator: true },
        { label: "Delete", icon: Trash2, danger: true, onClick: () => void doDelete(new Set([item.id])) },
      ];
    },
    [view, runAction, afterMutate, doDelete, openItem, downloadItems, toggleStar],
  );

  const blankMenuItems = useCallback(
    (): MenuItem[] => [
      { label: "New folder", icon: FolderPlus, onClick: () => setNewFolderOpen(true) },
      { label: "Upload files", icon: Upload, onClick: () => fileInputRef.current?.click() },
      { label: "Upload folder", icon: FolderPlus, onClick: () => folderInputRef.current?.click() },
      { separator: true },
      { label: "Refresh", icon: RefreshCw, onClick: () => void bump() },
      { separator: true },
      { label: "View as grid", icon: FolderPlus, onClick: () => setViewMode("grid") },
      { label: "View as list", icon: FolderPlus, onClick: () => setViewMode("list") },
    ],
    [bump],
  );

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && !typing) {
        e.preventDefault();
        setSelected(new Set(items.map((i) => i.id)));
        return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && !typing && selected.size > 0) {
        e.preventDefault();
        void doDelete(selected);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, selected]);

  // ── Render guards ─────────────────────────────────────────────────────────
  if (auth === null && !offline) {
    return (
      <div className="h-screen flex items-center justify-center text-[var(--text-muted)]">
        <div className="animate-pulse">Connecting to storage service…</div>
      </div>
    );
  }

  if (offline) {
    return (
      <OfflineState
        onRetry={() => {
          setOffline(false);
          setAuth(null);
          api.authStatus().then(setAuth).catch(() => setOffline(true));
        }}
      />
    );
  }

  if (auth && auth.protected && !auth.authenticated) {
    return (
      <LoginGate
        protectedRequired={auth.protected}
        onAuthenticated={() => {
          setAuth({ protected: true, authenticated: true });
          void bump();
        }}
      />
    );
  }

  const selectedItems = items.filter((i) => selected.has(i.id));
  const handleSearch = (q: string) => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (!q || q.length < 2) {
      setSearchResults([]);
      return;
    }
    searchTimerRef.current = setTimeout(async () => {
      try {
        const res = await api.search(q);
        setSearchResults(res.items);
      } catch { /* ignore */ }
    }, 250);
  };

  return (
    <div className="flex h-screen bg-[var(--bg)] text-[var(--text)] overflow-hidden">
      {/* Mobile drawer backdrop */}
      {sidebarOpen && (
        <div className="fixed inset-0 bg-black/50 z-30 md:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      {/* Sidebar */}
      <div className={`fixed inset-y-0 left-0 z-40 md:static md:inset-auto md:z-auto ${sidebarOpen ? "" : "hidden md:block"}`}>
        <Sidebar
          view={view}
          onView={(v) => { setSidebarOpen(false); navigate(v, null); }}
          totalFiles={stats.total_files}
          totalMb={stats.total_mb}
          syncing={syncing}
          onSync={async () => {
            setSyncing(true);
            try { await api.triggerSync(); } finally { setSyncing(false); }
          }}
          onClose={() => setSidebarOpen(false)}
        />
      </div>

      {/* Main column */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar
          viewMode={viewMode}
          onViewMode={setViewMode}
          liveConnected={liveConnected}
          searchRef={searchRef}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onSearchOpen={(item) => {
            if (item.type === "folder") {
              navigate("folder", item.id);
            } else {
              selectOnLoadRef.current = item.id;
              navigate("folder", item.parentId);
            }
          }}
          onSearch={handleSearch}
          searchResults={searchResults}
        />

        {/* Breadcrumbs + toolbar */}
        <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-[var(--border)] shrink-0 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <Breadcrumbs crumbs={breadcrumbs} onNavigate={(id) => navigate("folder", id)} />
            {selected.size > 0 && (
              <span className="text-xs bg-blue-500/15 text-blue-400 px-2 py-1 rounded-lg shrink-0">
                {selected.size} selected
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setDetailsItem(detailsItem ? null : selectedItems[0] ?? items[0] ?? null)}
              className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]"
              title="Details"
            >
              <Info size={15} />
            </button>
            <div className="relative">
              <button
                onClick={() => setNewMenuOpen((v) => !v)}
                className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-sm font-semibold transition-colors shadow-lg shadow-blue-500/20"
              >
                <Plus size={15} /> New
              </button>
              {newMenuOpen && (
                <div className="absolute right-0 top-full mt-1.5 w-48 bg-[var(--bg-elevated)] border border-[var(--border)] rounded-xl shadow-2xl py-1.5 z-50">
                  <button
                    onClick={() => { setNewMenuOpen(false); setNewFolderOpen(true); }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)]"
                  >
                    <FolderPlus size={14} /> New folder
                  </button>
                  <button
                    onClick={() => { setNewMenuOpen(false); fileInputRef.current?.click(); }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)]"
                  >
                    <Upload size={14} /> Upload files
                  </button>
                  <button
                    onClick={() => { setNewMenuOpen(false); folderInputRef.current?.click(); }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-[13px] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)]"
                  >
                    <FolderPlus size={14} /> Upload folder
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Selection toolbar */}
        {selected.size > 0 && (
          <SelectionToolbar
            count={selected.size}
            inTrash={view === "trash"}
            onDownload={() => downloadItems(selectedItems)}
            onMove={() => setMoveOpen(true)}
            onStar={() => {
              const target = selectedItems.find((i) => !i.starred) ?? selectedItems[0];
              if (target) void toggleStar(target);
            }}
            onDelete={() => void doDelete(selected)}
            onRestore={() => void runAction("restore", Array.from(selected)).then((ok) => { if (ok) afterMutate(); })}
            onDeletePermanent={() => void doDelete(selected)}
            onClear={() => setSelected(new Set())}
          />
        )}

        {/* Main file area */}
        <div
          className="flex-1 overflow-y-auto"
          onContextMenu={(e) => {
            e.preventDefault();
            setMenu({ x: e.clientX, y: e.clientY, item: null });
          }}
        >
          {loading ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 p-4">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-[var(--border)] overflow-hidden animate-pulse">
                  <div className="aspect-[4/3] bg-[var(--bg-soft)]" />
                  <div className="px-3 py-2.5 space-y-1.5">
                    <div className="h-3 bg-[var(--bg-soft)] rounded w-3/4" />
                    <div className="h-2.5 bg-[var(--bg-soft)] rounded w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : loadError ? (
            <ErrorState message={loadError} onRetry={() => void bump()} />
          ) : items.length === 0 ? (
            view === "folder" ? <EmptyFolder /> : <EmptyResults query="items" />
          ) : viewMode === "grid" ? (
            <FileGrid
              items={items}
              selected={selected}
              onSelect={handleSelect}
              onSelectRange={handleSelectRange}
              onOpen={openItem}
              onPreview={setPreviewItem}
              onContextMenu={(e, item) => setMenu({ x: e.clientX, y: e.clientY, item })}
              onDragStart={(item) => { dragItemIdRef.current = item.id; }}
              onDragOver={(item) => { dragItemIdRef.current = item.id; }}
              onDropOn={async (target) => {
                const moving = dragItemIdRef.current;
                dragItemIdRef.current = null;
                if (!moving || moving === target) return;
                const ok = await runAction("move", [moving], { parentId: target });
                if (ok) afterMutate();
              }}
              onAction={(action, item) => {
                if (action === "star" || action === "unstar") void toggleStar(item);
                else if (action === "delete") void doDelete(new Set([item.id]));
                else if (action === "preview") setPreviewItem(item);
                else if (action === "download") downloadItems([item]);
              }}
            />
          ) : (
            <FileTable
              items={items}
              selected={selected}
              sortKey={sortKey}
              sortDir={sortDir}
              onSort={(key) => {
                if (sortKey === key) setSortDir(sortDir === "asc" ? "desc" : "asc");
                else { setSortKey(key); setSortDir("asc"); }
              }}
              onSelect={handleSelect}
              onSelectRange={handleSelectRange}
              onOpen={openItem}
              onPreview={setPreviewItem}
              onContextMenu={(e, item) => setMenu({ x: e.clientX, y: e.clientY, item })}
              onDragStart={(item) => { dragItemIdRef.current = item.id; }}
              onDragOver={(item) => { dragItemIdRef.current = item.id; }}
              onDropOn={async (target) => {
                const moving = dragItemIdRef.current;
                dragItemIdRef.current = null;
                if (!moving || moving === target) return;
                const ok = await runAction("move", [moving], { parentId: target });
                if (ok) afterMutate();
              }}
              onAction={(action, item) => {
                if (action === "star" || action === "unstar") void toggleStar(item);
                else if (action === "delete") void doDelete(new Set([item.id]));
                else if (action === "preview") setPreviewItem(item);
                else if (action === "download") downloadItems([item]);
              }}
            />
          )}
        </div>

        {/* Hidden file inputs */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => { if (e.target.files) onDropFiles(e.target.files); e.target.value = ""; }}
        />
        <input
          ref={folderInputRef}
          type="file"
          multiple
          className="hidden"
          {...({ webkitdirectory: "" } as Record<string, string>)}
          onChange={(e) => { if (e.target.files) onDropFiles(e.target.files); e.target.value = ""; }}
        />

        <SyncStatusBar />
      </div>

      {/* Details panel */}
      {detailsItem && (
        <DetailsPanel item={detailsItem} onClose={() => setDetailsItem(null)} onToggleStar={toggleStar} />
      )}

      {/* Context menu */}
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          items={menu.item ? itemMenuItems(menu.item) : blankMenuItems()}
          onClose={() => setMenu(null)}
        />
      )}

      {/* Drop overlay */}
      <div
        className="fixed inset-0 z-[70] pointer-events-none"
        onDragOver={(e) => { e.preventDefault(); setDraggingOver(true); }}
        onDragLeave={() => setDraggingOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDraggingOver(false);
          if (e.dataTransfer.files.length > 0) onDropFiles(e.dataTransfer.files);
        }}
        style={{ pointerEvents: draggingOver ? "auto" : "none" }}
      >
        {draggingOver && (
          <div className="absolute inset-4 rounded-2xl border-2 border-dashed border-blue-500 bg-blue-500/10 backdrop-blur-sm flex items-center justify-center">
            <div className="text-center space-y-3">
              <Upload size={48} className="mx-auto text-blue-400" />
              <p className="text-white text-xl font-semibold">Drop files to upload</p>
            </div>
          </div>
        )}
      </div>

      {/* Dialogs */}
      <PreviewModal
        item={previewItem}
        items={items.filter((i) => i.type !== "folder")}
        onClose={() => setPreviewItem(null)}
        onNavigate={(dir) => {
          const fileItems = items.filter((i) => i.type !== "folder");
          const idx = previewItem ? fileItems.findIndex((i) => i.id === previewItem.id) : -1;
          const next = fileItems[idx + dir];
          if (next) setPreviewItem(next);
        }}
      />
      <NameDialog
        open={renameItem !== null}
        title="Rename"
        label="New name"
        initialValue={renameItem?.name ?? ""}
        submitLabel="Rename"
        onClose={() => setRenameItem(null)}
        onSubmit={async (name) => {
          if (!renameItem) return;
          const ok = await runAction("rename", [renameItem.id], { name });
          if (ok) afterMutate();
        }}
      />
      <NameDialog
        open={newFolderOpen}
        title="New folder"
        label="Folder name"
        initialValue=""
        submitLabel="Create"
        onClose={() => setNewFolderOpen(false)}
        onSubmit={async (name) => {
          const ok = await runAction("new-folder", [], { name, parent_folder_id: folderId });
          if (ok) afterMutate();
        }}
      />
      <MoveDialog
        open={moveOpen}
        items={selectedItems}
        onClose={() => setMoveOpen(false)}
        onMoved={() => afterMutate()}
      />
      <UploadManager
        jobs={uploads.jobs}
        open={uploadPanelOpen}
        onOpenChange={setUploadPanelOpen}
        onRetry={(id) => uploads.retry(id, folderId)}
        onCancel={uploads.cancel}
        onRemove={uploads.remove}
        onClearFinished={uploads.clearFinished}
        activeCount={uploads.activeCount}
      />
    </div>
  );
}
