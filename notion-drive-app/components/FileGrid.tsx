"use client";
import { AnimatePresence } from "framer-motion";
import FileCard, { type DriveItem } from "./FileCard";

interface FileGridProps {
  items: DriveItem[];
  selected: Set<string>;
  onSelect: (id: string, multi: boolean) => void;
  onOpen: (item: DriveItem) => void;
  onPreview: (item: DriveItem) => void;
  onAction: (action: string, item: DriveItem) => void;
  loading: boolean;
}

function Skeleton() {
  return (
    <div className="rounded-xl border border-white/[0.06] overflow-hidden animate-pulse">
      <div className="aspect-[4/3] bg-white/[0.04]" />
      <div className="px-3 py-2.5 space-y-1.5">
        <div className="h-3 bg-white/[0.06] rounded w-3/4" />
        <div className="h-2.5 bg-white/[0.04] rounded w-1/2" />
      </div>
    </div>
  );
}

export default function FileGrid({
  items, selected, onSelect, onOpen, onPreview, onAction, loading
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
          <div className="w-16 h-16 rounded-2xl bg-white/[0.04] border border-white/5 flex items-center justify-center mx-auto">
            <span className="text-3xl">📂</span>
          </div>
          <p className="text-gray-400 text-sm">This folder is empty</p>
          <p className="text-gray-600 text-xs">Drop files here or use the upload button</p>
        </div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 p-4">
      <AnimatePresence mode="popLayout">
        {items.map((item) => (
          <FileCard
            key={item.id}
            item={item}
            selected={selected.has(item.id)}
            onSelect={onSelect}
            onOpen={onOpen}
            onPreview={onPreview}
            onAction={onAction}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}
