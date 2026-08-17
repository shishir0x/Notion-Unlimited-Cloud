"use client";

import { Archive, Download, FolderInput, RotateCcw, Star, Trash2, X } from "lucide-react";

interface SelectionToolbarProps {
  count: number;
  inTrash: boolean;
  onDownload: () => void;
  onMove: () => void;
  onStar: () => void;
  onDelete: () => void;
  onRestore: () => void;
  onDeletePermanent: () => void;
  onClear: () => void;
}

export default function SelectionToolbar({
  count, inTrash, onDownload, onMove, onStar, onDelete, onRestore, onDeletePermanent, onClear,
}: SelectionToolbarProps) {
  if (count === 0) return null;

  const btn =
    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)]";

  return (
    <div className="flex items-center gap-1 flex-wrap px-4 py-2 bg-[var(--bg-soft)] border-b border-[var(--border)]">
      <span className="text-[13px] font-medium text-[var(--text)] mr-2">
        {count} selected
      </span>
      <button onClick={onClear} className={`${btn} !text-[var(--text-muted)]`} title="Clear selection">
        <X size={13} /> Clear
      </button>
      <div className="w-px h-5 bg-[var(--border)] mx-1" />
      {!inTrash ? (
        <>
          <button onClick={onDownload} className={btn} disabled={count === 0}>
            <Download size={13} /> Download
          </button>
          <button onClick={onMove} className={btn}>
            <FolderInput size={13} /> Move
          </button>
          <button onClick={onStar} className={btn}>
            <Star size={13} /> Star
          </button>
          <button onClick={onDelete} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors text-red-400 hover:bg-red-500/10">
            <Trash2 size={13} /> Delete
          </button>
        </>
      ) : (
        <>
          <button onClick={onRestore} className={btn}>
            <RotateCcw size={13} /> Restore
          </button>
          <button onClick={onDeletePermanent} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors text-red-400 hover:bg-red-500/10">
            <Archive size={13} /> Delete permanently
          </button>
        </>
      )}
    </div>
  );
}
