"use client";
import { useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, ChevronLeft, ChevronRight, ExternalLink, Download, Volume2 } from "lucide-react";
import type { DriveItem } from "./FileCard";

interface PreviewModalProps {
  item: DriveItem | null;
  items: DriveItem[];
  onClose: () => void;
  onNavigate: (dir: -1 | 1) => void;
}

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"]);
const VIDEO_EXTS = new Set([".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"]);
const AUDIO_EXTS = new Set([".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"]);
const PDF_EXT = ".pdf";
const CODE_EXTS = new Set([".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".java", ".c", ".cpp", ".sh", ".json", ".yaml", ".md", ".html", ".css", ".txt", ".log"]);

function PreviewContent({ item }: { item: DriveItem }) {
  const ext = item.extension.toLowerCase();
  const viewUrl = `/api/view?id=${item.id}`;

  if (IMAGE_EXTS.has(ext)) {
    const src = item.fileUrl ?? viewUrl;
    return (
      <div className="flex-1 flex items-center justify-center p-6 min-h-0">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={item.name}
          className="max-w-full max-h-full object-contain rounded-lg shadow-2xl"
        />
      </div>
    );
  }

  if (VIDEO_EXTS.has(ext)) {
    const src = item.fileUrl ?? viewUrl;
    return (
      <div className="flex-1 flex items-center justify-center p-6 min-h-0">
        <video
          src={src}
          controls
          autoPlay={false}
          className="max-w-full max-h-full rounded-lg shadow-2xl outline-none"
          style={{ background: "#000" }}
        />
      </div>
    );
  }

  if (AUDIO_EXTS.has(ext)) {
    const src = item.fileUrl ?? viewUrl;
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-10 gap-6">
        <div className="w-28 h-28 rounded-3xl bg-gradient-to-br from-amber-500/30 to-yellow-600/10 flex items-center justify-center border border-amber-500/20">
          <Volume2 size={52} className="text-amber-400" />
        </div>
        <p className="text-white/60 text-sm">{item.name}</p>
        <audio src={src} controls className="w-full max-w-sm" />
      </div>
    );
  }

  if (ext === PDF_EXT) {
    const src = item.fileUrl ?? viewUrl;
    return (
      <iframe
        src={src}
        className="flex-1 w-full border-0 bg-white rounded-b-2xl"
        title={item.name}
      />
    );
  }

  if (CODE_EXTS.has(ext)) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 gap-4">
        <a
          href={viewUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 px-5 py-3 bg-blue-600 hover:bg-blue-500 text-white rounded-xl font-semibold transition-colors"
        >
          <ExternalLink size={16} /> Open in Browser
        </a>
        <a href={item.notionUrl} target="_blank" rel="noreferrer" className="text-gray-400 hover:text-white text-sm transition-colors">
          View in Notion →
        </a>
      </div>
    );
  }

  // Generic fallback
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4">
      <div className="text-6xl">📄</div>
      <p className="text-gray-400 text-sm">{item.name}</p>
      <div className="flex gap-3">
        <a href={viewUrl} target="_blank" rel="noreferrer" className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm">Open</a>
        <a href={item.notionUrl} target="_blank" rel="noreferrer" className="px-4 py-2 bg-white/10 text-white rounded-lg text-sm">Notion</a>
      </div>
    </div>
  );
}

export default function PreviewModal({ item, items, onClose, onNavigate }: PreviewModalProps) {
  const idx = item ? items.findIndex((i) => i.id === item.id) : -1;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!item) return;
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onNavigate(-1);
      if (e.key === "ArrowRight") onNavigate(1);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [item, onClose, onNavigate]);

  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  }, [onClose]);

  return (
    <AnimatePresence>
      {item && (
        <motion.div
          key="backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
          onClick={handleBackdropClick}
        >
          <motion.div
            key="modal"
            initial={{ opacity: 0, scale: 0.95, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 10 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="bg-[#141618] border border-white/10 rounded-2xl shadow-2xl w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center gap-3 px-5 py-3.5 border-b border-white/[0.06] shrink-0">
              <div className="flex-1 min-w-0">
                <p className="text-white font-medium text-[14px] truncate">{item.name}</p>
                <p className="text-gray-500 text-[11px] mt-0.5">
                  {item.sizeMb.toFixed(2)} MB · {item.extension.toUpperCase() || "File"}
                  {items.length > 1 && ` · ${idx + 1} / ${items.length}`}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <a
                  href={`/api/view?id=${item.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
                  title="Open in browser"
                >
                  <ExternalLink size={15} />
                </a>
                {item.fileUrl && (
                  <a
                    href={item.fileUrl}
                    download
                    className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
                    title="Download"
                  >
                    <Download size={15} />
                  </a>
                )}
                <button onClick={onClose} className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors">
                  <X size={15} />
                </button>
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 flex min-h-0 relative">
              <PreviewContent item={item} />

              {/* Prev / Next nav */}
              {items.length > 1 && (
                <>
                  <button
                    onClick={() => onNavigate(-1)}
                    disabled={idx === 0}
                    className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-black/60 border border-white/10 flex items-center justify-center text-white hover:bg-black/80 disabled:opacity-20 transition-all z-10"
                  >
                    <ChevronLeft size={18} />
                  </button>
                  <button
                    onClick={() => onNavigate(1)}
                    disabled={idx === items.length - 1}
                    className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-black/60 border border-white/10 flex items-center justify-center text-white hover:bg-black/80 disabled:opacity-20 transition-all z-10"
                  >
                    <ChevronRight size={18} />
                  </button>
                </>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
