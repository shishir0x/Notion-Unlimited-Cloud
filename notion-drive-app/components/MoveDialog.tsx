"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Folder, FolderInput, HardDrive, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { DriveItem, Breadcrumb } from "@/lib/types";

interface MoveDialogProps {
  open: boolean;
  items: DriveItem[];
  onClose: () => void;
  onMoved: () => void;
}

export default function MoveDialog({ open, items, onClose, onMoved }: MoveDialogProps) {
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [crumbs, setCrumbs] = useState<Breadcrumb[]>([]);
  const [folders, setFolders] = useState<DriveItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (folderId: string | null) => {
    setLoading(true);
    setError("");
    try {
      const data = await api.drive(folderId, "name", "asc");
      setFolders(data.items.filter((i) => i.type === "folder" && !i.archived && i.id !== items[0]?.id));
      setCrumbs(data.breadcrumbs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load folders");
    } finally {
      setLoading(false);
    }
  }, [items]);

  useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCurrentId(null);
      void load(null);
    }
  }, [open, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const openFolder = (id: string) => {
    setCurrentId(id);
    void load(id);
  };

  const goToCrumb = (id: string | null) => {
    setCurrentId(id);
    void load(id);
  };

  const move = async () => {
    if (items.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const ids = items.map((i) => i.id);
      const res = await api.action("move", ids, { parentId: currentId });
      if (!res.success) {
        setError(res.error ?? "Move failed");
        return;
      }
      onMoved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Move failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-md bg-[var(--bg-elevated)] border border-[var(--border)] rounded-2xl shadow-2xl overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--border)]">
          <h2 className="text-sm font-semibold text-[var(--text)] flex items-center gap-2">
            <FolderInput size={15} className="text-[var(--text-muted)]" />
            Move {items.length === 1 ? `"${items[0].name}"` : `${items.length} items`}
          </h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)]">
            <X size={14} />
          </button>
        </div>

        {/* Breadcrumbs */}
        <div className="flex items-center gap-1 px-5 py-2.5 border-b border-[var(--border)] text-xs overflow-x-auto no-scrollbar">
          <button onClick={() => goToCrumb(null)} className="flex items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--text)] shrink-0">
            <HardDrive size={12} className="text-blue-400" /> My Drive
          </button>
          {crumbs.map((c) => (
            <span key={c.id ?? "root"} className="flex items-center gap-1 shrink-0">
              <ChevronRight size={11} className="text-[var(--text-muted)]" />
              <button onClick={() => goToCrumb(c.id)} className="text-[var(--text-secondary)] hover:text-[var(--text)]">
                {c.name}
              </button>
            </span>
          ))}
        </div>

        {/* Folder list */}
        <div className="max-h-64 overflow-y-auto p-2">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-[var(--text-muted)]">
              <Loader2 size={16} className="animate-spin" />
            </div>
          ) : folders.length === 0 ? (
            <p className="text-center py-10 text-[var(--text-muted)] text-sm">No subfolders here</p>
          ) : (
            folders.map((f) => (
              <button
                key={f.id}
                onClick={() => openFolder(f.id)}
                className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg hover:bg-[var(--bg-hover)] text-left transition-colors"
              >
                <Folder size={16} className="text-amber-400 shrink-0" fill="currentColor" />
                <span className="text-sm text-[var(--text)] truncate flex-1">{f.name}</span>
                <span className="text-[var(--text-muted)] text-[11px]">{f.itemCount} items</span>
              </button>
            ))
          )}
        </div>

        {error && <p className="px-5 pb-2 text-xs text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 px-5 py-3.5 border-t border-[var(--border)]">
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]">
            Cancel
          </button>
          <button
            onClick={move}
            disabled={busy}
            className="px-4 py-2 rounded-xl text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
          >
            {busy ? "Moving…" : "Move here"}
          </button>
        </div>
      </div>
    </div>
  );
}
