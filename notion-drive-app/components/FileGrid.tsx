"use client";
import { AnimatePresence } from "framer-motion";
import FileCard from "./FileCard";
import type { DriveItem } from "@/lib/types";

interface FileGridProps {
  items: DriveItem[];
  selected: Set<string>;
  onSelect: (id: string, multi: boolean) => void;
  onSelectRange: (id: string) => void;
  onOpen: (item: DriveItem) => void;
  onPreview: (item: DriveItem) => void;
  onAction: (action: string, item: DriveItem) => void;
  onContextMenu: (e: React.MouseEvent, item: DriveItem) => void;
  onDragStart: (item: DriveItem) => void;
  onDragOver: (item: DriveItem) => void;
  onDropOn: (folderId: string) => void;
  loading?: boolean;
}

function Skeleton() {
  return (
    <div className="rounded-xl border border-[var(--border)] overflow-hidden animate-pulse">
      <div className="aspect-[4/3] bg-[var(--bg-soft)]" />
      <div className="px-3 py-2.5 space-y-1.5">
        <div className="h-3 bg-[var(--bg-soft)] rounded w-3/4" />
        <div className="h-2.5 bg-[var(--bg-soft)] rounded w-1/2" />
      </div>
    </div>
  );
}

export default function FileGrid({
  items, selected, onSelect, onSelectRange, onOpen, onPreview, onAction,
  onContextMenu, onDragStart, onDragOver, onDropOn, loading,
}: FileGridProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 p-4">
        {Array.from({ length: 18 }).map((_, i) => <Skeleton key={i} />)}
      </div>
    );
  }

  if (!loading && items.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center py-24">
        <div className="text-center space-y-3">
          <div className="w-16 h-16 rounded-2xl bg-[var(--bg-soft)] border border-[var(--border)] flex items-center justify-center mx-auto">
            <span className="text-3xl">📂</span>
          </div>
          <p className="text-[var(--text-secondary)] text-sm">This folder is empty</p>
          <p className="text-[var(--text-muted)] text-xs">Drop files here or use the upload button</p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 p-4">
      <AnimatePresence mode="popLayout">
        {items.map((item) => (
          <div
            key={item.id}
            draggable={item.type === "file"}
            onDragStart={(e) => { e.stopPropagation(); onDragStart(item); }}
            onDragOver={(e) => { if (item.type === "folder") { e.preventDefault(); e.stopPropagation(); onDragOver(item); } }}
            onDrop={(e) => { if (item.type === "folder") { e.preventDefault(); e.stopPropagation(); onDropOn(item.id); } }}
            onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContextMenu(e, item); }}
          >
            <FileCard
              item={item}
              selected={selected.has(item.id)}
              onSelect={onSelect}
              onSelectRange={onSelectRange}
              onOpen={onOpen}
              onPreview={onPreview}
              onAction={onAction}
            />
          </div>
        ))}
      </AnimatePresence>
    </div>
  );
}
