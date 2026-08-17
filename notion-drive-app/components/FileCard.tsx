"use client";
import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Folder, Image as ImageIcon, Film, FileText, Code2, Music, File,
  Star, MoreVertical, ExternalLink, Download, Eye, Trash2
} from "lucide-react";
import type { DriveItem } from "@/lib/types";

interface FileCardProps {
  item: DriveItem;
  selected: boolean;
  onSelect: (id: string, multi: boolean) => void;
  onSelectRange?: (id: string) => void;
  onOpen: (item: DriveItem) => void;
  onPreview: (item: DriveItem) => void;
  onAction: (action: string, item: DriveItem) => void;
}

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"]);
const VIDEO_EXTS = new Set([".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"]);
const AUDIO_EXTS = new Set([".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"]);
const CODE_EXTS = new Set([".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".java", ".c", ".cpp", ".sh", ".json", ".yaml", ".yml", ".toml", ".md", ".html", ".css"]);

function Thumbnail({ item }: { item: DriveItem }) {
  const ext = item.extension.toLowerCase();
  const [imgError, setImgError] = useState(false);
  const viewUrl = `/api/view?id=${item.id}`;

  if (item.type === "folder") {
    return (
      <div className="w-full h-full flex items-center justify-center bg-gradient-to-br from-amber-500/20 to-orange-600/10">
        <Folder size={36} className="text-amber-400" fill="currentColor" />
      </div>
    );
  }

  // Image thumbnail
  if (IMAGE_EXTS.has(ext) && !imgError) {
    return (
      <div className="w-full h-full relative overflow-hidden">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={viewUrl}
          alt={item.name}
          className="w-full h-full object-cover"
          onError={() => setImgError(true)}
          loading="lazy"
        />
      </div>
    );
  }
  if (IMAGE_EXTS.has(ext)) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-gradient-to-br from-violet-500/20 to-purple-700/10 gap-2">
        <ImageIcon size={32} className="text-violet-400" />
        <span className="text-[9px] font-bold text-violet-300 uppercase tracking-wider bg-violet-500/20 px-2 py-0.5 rounded">
          {ext.slice(1)}
        </span>
      </div>
    );
  }

  // Video
  if (VIDEO_EXTS.has(ext)) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-gradient-to-br from-blue-900/40 to-indigo-900/20 gap-2">
        <div className="w-10 h-10 rounded-full bg-white/10 backdrop-blur flex items-center justify-center border border-white/10">
          <Film size={18} className="text-blue-300" />
        </div>
        <span className="text-[9px] font-bold text-blue-300 uppercase tracking-wider bg-blue-500/20 px-2 py-0.5 rounded">
          {ext.slice(1)}
        </span>
      </div>
    );
  }

  // Audio
  if (AUDIO_EXTS.has(ext)) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-gradient-to-br from-amber-500/20 to-yellow-600/10 gap-2">
        <Music size={32} className="text-amber-400" />
        <span className="text-[9px] font-bold text-amber-300 uppercase bg-amber-500/20 px-2 py-0.5 rounded">
          {ext.slice(1)}
        </span>
      </div>
    );
  }

  // PDF
  if (ext === ".pdf") {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-gradient-to-br from-red-500/20 to-rose-700/10 gap-2">
        <FileText size={32} className="text-red-400" />
        <span className="text-[9px] font-bold text-red-300 uppercase bg-red-500/20 px-2 py-0.5 rounded">PDF</span>
      </div>
    );
  }

  // Code
  if (CODE_EXTS.has(ext)) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center bg-gradient-to-br from-emerald-500/20 to-green-700/10 gap-2">
        <Code2 size={28} className="text-emerald-400" />
        <span className="text-[9px] font-bold text-emerald-300 bg-emerald-500/20 px-2 py-0.5 rounded uppercase">
          {ext.slice(1) || "CODE"}
        </span>
      </div>
    );
  }

  return (
    <div className="w-full h-full flex flex-col items-center justify-center bg-white/[0.03] gap-2">
      <File size={30} className="text-gray-500" />
      {ext && (
        <span className="text-[9px] font-bold text-gray-500 uppercase bg-white/5 px-2 py-0.5 rounded">
          {ext.slice(1)}
        </span>
      )}
    </div>
  );
}

export default function FileCard({ item, selected, onSelect, onSelectRange, onOpen, onPreview, onAction }: FileCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  const handleClick = useCallback((e: React.MouseEvent) => {
    if (e.shiftKey && onSelectRange) {
      onSelectRange(item.id);
    } else if (e.ctrlKey || e.metaKey) {
      onSelect(item.id, true);
    } else {
      onSelect(item.id, false);
    }
  }, [item.id, onSelect, onSelectRange]);

  const handleDoubleClick = useCallback(() => {
    onOpen(item);
  }, [item, onOpen]);

  const dateStr = new Date(item.modifiedAt).toLocaleDateString("en-US", {
    month: "short", day: "numeric"
  });

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.15 }}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      className={`relative group rounded-xl border cursor-pointer select-none transition-all duration-150 overflow-hidden ${
        selected
          ? "border-blue-500/60 bg-blue-500/10 ring-1 ring-blue-500/20"
          : "border-white/[0.06] bg-white/[0.03] hover:border-white/10 hover:bg-white/[0.06]"
      }`}
    >
      {/* Thumbnail */}
      <div className="aspect-[4/3] w-full overflow-hidden bg-[#161820]">
        <Thumbnail item={item} />
      </div>

      {/* Star badge */}
      {item.starred && (
        <div className="absolute top-2 left-2">
          <Star size={13} className="text-amber-400 fill-amber-400" />
        </div>
      )}

      {/* Select checkbox */}
      <div
        className={`absolute top-2 right-2 w-5 h-5 rounded-md border flex items-center justify-center transition-all duration-100 ${
          selected
            ? "bg-blue-500 border-blue-500"
            : "bg-black/40 border-white/20 opacity-0 group-hover:opacity-100"
        }`}
        onClick={(e) => { e.stopPropagation(); onSelect(item.id, true); }}
      >
        {selected && <span className="text-white text-[10px] font-bold">✓</span>}
      </div>

      {/* Info */}
      <div className="px-3 py-2.5">
        <p className="text-[13px] text-white/90 font-medium truncate leading-snug">{item.name}</p>
        <p className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-1.5">
          {item.type === "folder" ? "Folder" : `${item.sizeMb.toFixed(1)} MB`}
          <span className="text-gray-700">·</span>
          {dateStr}
        </p>
      </div>

      {/* Hover action bar */}
      <div className="absolute inset-x-0 bottom-0 opacity-0 group-hover:opacity-100 transition-opacity duration-150 bg-gradient-to-t from-black/80 to-transparent px-3 py-2 flex items-center gap-1.5">
        <button
          onClick={(e) => { e.stopPropagation(); onPreview(item); }}
          className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
          title="Preview"
        >
          <Eye size={13} />
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); window.open(item.notionUrl, "_blank"); }}
          className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
          title="Open in Notion"
        >
          <ExternalLink size={13} />
        </button>
        <a
          href={`/api/download?id=${item.id}&local_path=${encodeURIComponent(item.localPath)}`}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
          title="Download"
        >
          <Download size={13} />
        </a>
        <button
          onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
          className="p-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors ml-auto"
        >
          <MoreVertical size={13} />
        </button>
      </div>

      {/* Context menu */}
      <AnimatePresence>
        {menuOpen && (
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className="absolute bottom-12 right-2 z-50 bg-[#1e2023] border border-white/10 rounded-xl shadow-xl shadow-black/50 py-1.5 min-w-[160px]"
            onClick={(e) => e.stopPropagation()}
          >
            {[
              { label: "Preview", icon: Eye, action: () => { setMenuOpen(false); onPreview(item); } },
              { label: "Open in Notion", icon: ExternalLink, action: () => { setMenuOpen(false); window.open(item.notionUrl, "_blank"); } },
              { label: item.starred ? "Unstar" : "Star", icon: Star, action: () => { setMenuOpen(false); onAction(item.starred ? "unstar" : "star", item); } },
              { label: "Delete", icon: Trash2, action: () => { setMenuOpen(false); onAction("delete", item); }, danger: true },
            ].map(({ label, icon: Icon, action, danger }) => (
              <button
                key={label}
                onClick={action}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] transition-colors ${
                  danger ? "text-red-400 hover:bg-red-500/10" : "text-gray-300 hover:bg-white/5"
                }`}
              >
                <Icon size={13} />
                {label}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
