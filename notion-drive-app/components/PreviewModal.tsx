"use client";
import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X, ChevronLeft, ChevronRight, ExternalLink, Download,
  Volume2, Copy, Check, FileText, Loader2, Image as ImageIcon,
  Film, Music, Sparkles, HardDrive, Smartphone
} from "lucide-react";
import type { DriveItem } from "./FileCard";

interface PreviewModalProps {
  item: DriveItem | null;
  items: DriveItem[];
  onClose: () => void;
  onNavigate: (dir: -1 | 1) => void;
}

const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif", ".ico"]);
const VIDEO_EXTS = new Set([".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"]);
const AUDIO_EXTS = new Set([".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"]);
const PDF_EXT = ".pdf";
const CODE_EXTS = new Set([
  ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".java", ".c", ".cpp",
  ".h", ".sh", ".bat", ".json", ".yaml", ".yml", ".toml", ".md", ".html",
  ".css", ".txt", ".log", ".sql", ".ini", ".env", ".xml", ".tachibk"
]);

// ── Google Cloud / OneDrive Photo Presentation View ────────────────────────
function CloudPhotoStage({ item }: { item: DriveItem }) {
  const isPhone = item.description?.includes("Internal shared storage") || item.description?.includes("CPH2613");

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 sm:p-10 bg-gradient-to-b from-[#0e1117] via-[#090b0e] to-[#060709] text-center overflow-y-auto relative">
      {/* Ambient lens flare effect */}
      <div className="absolute w-72 h-72 rounded-full bg-blue-500/10 blur-3xl pointer-events-none -top-10" />

      {/* Main Glassmorphic Photo Card */}
      <div className="relative w-full max-w-md bg-white/[0.03] border border-white/10 rounded-2xl p-6 sm:p-8 backdrop-blur-xl shadow-2xl flex flex-col items-center">
        {/* Photo Icon Badge */}
        <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-blue-600/30 to-indigo-600/20 border border-blue-500/30 flex items-center justify-center text-blue-400 shadow-xl mb-4">
          <ImageIcon size={40} className="drop-shadow" />
        </div>

        <h3 className="text-white font-semibold text-base sm:text-lg max-w-full truncate px-2" title={item.name}>
          {item.name}
        </h3>
        <p className="text-blue-400/80 text-xs font-medium mt-1 flex items-center gap-1.5">
          <Sparkles size={12} /> Indexed in Notion Cloud Database
        </p>

        {/* Technical Specs Table */}
        <div className="grid grid-cols-2 gap-3 mt-6 w-full text-left text-xs bg-black/40 border border-white/[0.06] p-3.5 rounded-xl">
          <div>
            <span className="text-gray-500 block text-[11px]">File Format</span>
            <span className="text-gray-200 font-medium uppercase font-mono">{item.extension.slice(1) || "IMAGE"}</span>
          </div>
          <div>
            <span className="text-gray-500 block text-[11px]">Size</span>
            <span className="text-gray-200 font-medium">{item.sizeMb.toFixed(2)} MB</span>
          </div>
          <div>
            <span className="text-gray-500 block text-[11px]">Source</span>
            <span className="text-gray-200 font-medium flex items-center gap-1">
              {isPhone ? <Smartphone size={12} className="text-emerald-400" /> : <HardDrive size={12} className="text-blue-400" />}
              {isPhone ? "Android Device" : "Local Disk"}
            </span>
          </div>
          <div>
            <span className="text-gray-500 block text-[11px]">Modified</span>
            <span className="text-gray-200 font-medium">
              {new Date(item.modifiedAt).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
            </span>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center justify-center gap-3 mt-6 w-full">
          <a
            href={`/api/view?id=${item.id}&download=1`}
            download={item.name}
            className="flex-1 min-w-[140px] inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-semibold shadow-lg shadow-blue-600/30 transition-all active:scale-[0.98]"
          >
            <Download size={14} /> Download File
          </a>
          <a
            href={item.notionUrl}
            target="_blank"
            rel="noreferrer"
            className="flex-1 min-w-[140px] inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-white/10 hover:bg-white/15 text-white rounded-xl text-xs font-semibold transition-all active:scale-[0.98]"
          >
            <ExternalLink size={14} /> Open in Notion
          </a>
        </div>
      </div>
    </div>
  );
}

// ── Generic File Presentation View ─────────────────────────────────────────
function CloudGenericStage({ item, icon: Icon = FileText }: { item: DriveItem; icon?: any }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 sm:p-10 bg-gradient-to-b from-[#0e1117] to-[#060709] text-center overflow-y-auto">
      <div className="w-20 h-20 rounded-2xl bg-white/[0.04] border border-white/10 flex items-center justify-center text-gray-300 shadow-xl mb-4">
        <Icon size={38} />
      </div>
      <h3 className="text-white font-semibold text-base sm:text-lg max-w-md truncate px-2">{item.name}</h3>
      <p className="text-gray-400 text-xs mt-1">{item.sizeMb.toFixed(2)} MB · {item.fileType}</p>

      <div className="flex flex-wrap items-center justify-center gap-3 mt-6">
        <a
          href={`/api/view?id=${item.id}&download=1`}
          download={item.name}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-semibold shadow-lg shadow-blue-600/20 transition-all"
        >
          <Download size={14} /> Download File
        </a>
        <a
          href={item.notionUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-white/10 hover:bg-white/15 text-white rounded-xl text-xs font-semibold transition-all"
        >
          <ExternalLink size={14} /> Open in Notion
        </a>
      </div>
    </div>
  );
}

// ── Image Preview Component ────────────────────────────────────────────────
function ImagePreview({ item }: { item: DriveItem }) {
  const [loading, setLoading] = useState(true);
  const [hasBinary, setHasBinary] = useState(Boolean(item.fileUrl));

  if (!hasBinary) {
    return <CloudPhotoStage item={item} />;
  }

  return (
    <div className="flex-1 flex items-center justify-center p-6 min-h-0 bg-black/70 relative overflow-hidden">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-sm z-10">
          <Loader2 size={32} className="animate-spin text-blue-400" />
        </div>
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={item.fileUrl || `/api/view?id=${item.id}`}
        alt={item.name}
        className={`max-w-full max-h-full object-contain rounded-lg shadow-2xl transition-opacity duration-200 ${
          loading ? "opacity-0" : "opacity-100"
        }`}
        onLoad={() => setLoading(false)}
        onError={() => {
          setLoading(false);
          setHasBinary(false);
        }}
      />
    </div>
  );
}

// ── Video Preview Component ────────────────────────────────────────────────
function VideoPreview({ item }: { item: DriveItem }) {
  const [error, setError] = useState(false);
  const src = item.fileUrl || `/api/view?id=${item.id}`;

  if (error || !item.fileUrl) {
    return <CloudGenericStage item={item} icon={Film} />;
  }

  return (
    <div className="flex-1 flex items-center justify-center p-6 min-h-0 bg-black">
      <video
        src={src}
        controls
        autoPlay={false}
        className="max-w-full max-h-full rounded-lg shadow-2xl outline-none"
        onError={() => setError(true)}
      />
    </div>
  );
}

// ── Audio Preview Component ────────────────────────────────────────────────
function AudioPreview({ item }: { item: DriveItem }) {
  const [error, setError] = useState(false);
  const src = item.fileUrl || `/api/view?id=${item.id}`;

  if (error || !item.fileUrl) {
    return <CloudGenericStage item={item} icon={Music} />;
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center p-10 gap-6 bg-gradient-to-b from-[#181a1f] to-[#121417]">
      <div className="w-28 h-28 rounded-3xl bg-gradient-to-br from-amber-500/30 to-yellow-600/10 flex items-center justify-center border border-amber-500/20 shadow-2xl">
        <Volume2 size={52} className="text-amber-400" />
      </div>
      <div className="text-center">
        <p className="text-white font-medium text-base">{item.name}</p>
        <p className="text-gray-400 text-xs mt-1">{item.sizeMb.toFixed(2)} MB</p>
      </div>
      <audio
        src={src}
        controls
        className="w-full max-w-md shadow-lg"
        onError={() => setError(true)}
      />
    </div>
  );
}

// ── Text / Code Preview ─────────────────────────────────────────────────────
function TextCodePreview({ item }: { item: DriveItem }) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchText = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/view?id=${item.id}&mode=text`);
      if (!res.ok) throw new Error("Could not retrieve text content");
      const data = await res.json();
      if (data.content) {
        setContent(data.content);
      } else {
        throw new Error("No text blocks in Notion");
      }
    } catch (err: any) {
      setError(err.message || "Failed to load content");
    } finally {
      setLoading(false);
    }
  }, [item.id]);

  useEffect(() => {
    fetchText();
  }, [fetchText]);

  const handleCopy = () => {
    if (!content) return;
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-12 text-gray-400">
        <Loader2 size={32} className="animate-spin text-blue-500" />
        <p className="text-sm">Reading Notion database page...</p>
      </div>
    );
  }

  if (error || !content) {
    return <CloudGenericStage item={item} icon={FileText} />;
  }

  const lines = content.split("\n");

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[#0d0f12] text-gray-200">
      {/* Code Header Bar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-white/[0.03] border-b border-white/[0.06] text-xs">
        <span className="text-gray-400 font-mono flex items-center gap-2">
          <Sparkles size={13} className="text-blue-400" />
          {lines.length} lines · {(new Blob([content]).size / 1024).toFixed(1)} KB
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 px-3 py-1 bg-white/10 hover:bg-white/20 rounded-lg text-white font-medium transition-colors"
        >
          {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
          {copied ? "Copied" : "Copy Code"}
        </button>
      </div>

      {/* Code Content with Line Numbers */}
      <div className="flex-1 overflow-auto p-4 font-mono text-[13px] leading-relaxed select-text">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((line, idx) => (
              <tr key={idx} className="hover:bg-white/[0.04]">
                <td className="w-12 pr-4 text-right select-none text-gray-600 text-[11px] align-top py-0.5">
                  {idx + 1}
                </td>
                <td className="whitespace-pre-wrap break-all text-gray-300 py-0.5">
                  {line || "\n"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Preview Content Router ──────────────────────────────────────────────────
function PreviewContent({ item }: { item: DriveItem }) {
  const ext = item.extension.toLowerCase();

  if (IMAGE_EXTS.has(ext)) {
    return <ImagePreview item={item} />;
  }

  if (VIDEO_EXTS.has(ext)) {
    return <VideoPreview item={item} />;
  }

  if (AUDIO_EXTS.has(ext)) {
    return <AudioPreview item={item} />;
  }

  if (ext === PDF_EXT) {
    if (item.fileUrl) {
      return (
        <iframe
          src={item.fileUrl}
          className="flex-1 w-full border-0 bg-white rounded-b-2xl"
          title={item.name}
        />
      );
    }
    return <CloudGenericStage item={item} icon={FileText} />;
  }

  if (CODE_EXTS.has(ext) || ext === "") {
    return <TextCodePreview item={item} />;
  }

  return <CloudGenericStage item={item} icon={FileText} />;
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
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 sm:p-6"
          onClick={handleBackdropClick}
        >
          <motion.div
            key="modal"
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="bg-[#121417] border border-white/10 rounded-2xl shadow-2xl shadow-black/80 w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center gap-3 px-5 py-3.5 border-b border-white/[0.06] bg-[#16181c] shrink-0">
              <div className="flex-1 min-w-0">
                <p className="text-white font-medium text-[14px] truncate">{item.name}</p>
                <p className="text-gray-500 text-[11px] mt-0.5">
                  {item.sizeMb.toFixed(2)} MB · {item.extension.toUpperCase() || "File"}
                  {items.length > 1 && ` · ${idx + 1} of ${items.length}`}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <a
                  href={`/api/view?id=${item.id}&download=1`}
                  download={item.name}
                  className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
                  title="Download File"
                >
                  <Download size={16} />
                </a>
                <a
                  href={item.notionUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
                  title="Open in Notion"
                >
                  <ExternalLink size={16} />
                </a>
                <button
                  onClick={onClose}
                  className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors ml-1"
                >
                  <X size={17} />
                </button>
              </div>
            </div>

            {/* In-Tab Content */}
            <div className="flex-1 flex min-h-0 relative overflow-hidden">
              <PreviewContent item={item} />

              {/* Prev / Next nav */}
              {items.length > 1 && (
                <>
                  <button
                    onClick={() => onNavigate(-1)}
                    disabled={idx === 0}
                    className="absolute left-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-black/70 border border-white/10 flex items-center justify-center text-white hover:bg-black/90 disabled:opacity-10 transition-all z-10 shadow-lg"
                  >
                    <ChevronLeft size={18} />
                  </button>
                  <button
                    onClick={() => onNavigate(1)}
                    disabled={idx === items.length - 1}
                    className="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 rounded-full bg-black/70 border border-white/10 flex items-center justify-center text-white hover:bg-black/90 disabled:opacity-10 transition-all z-10 shadow-lg"
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
