"use client";

import { X, HardDrive, Star } from "lucide-react";
import type { DriveItem } from "@/lib/types";
import { formatBytes, formatDateTime, formatRelative } from "@/lib/format";
import { getFileType } from "@/lib/file-types";
import { sourceLabel } from "@/lib/paths";

interface DetailsPanelProps {
  item: DriveItem;
  onClose: () => void;
  onToggleStar: (item: DriveItem) => void;
}

function Row({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <div className="py-2">
      <dt className="text-[11px] uppercase tracking-wide text-[var(--text-muted)] mb-0.5">{label}</dt>
      <dd className={`text-[13px] text-[var(--text-secondary)] break-words ${mono ? "font-mono text-[12px]" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

export default function DetailsPanel({ item, onClose, onToggleStar }: DetailsPanelProps) {
  const info = getFileType(item.extension);
  const Icon = item.type === "folder" ? HardDrive : info.icon;

  return (
    <aside className="w-72 shrink-0 border-l border-[var(--border)] bg-[var(--bg-elevated)] flex flex-col overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
        <span className="text-sm font-medium text-[var(--text)]">Details</span>
        <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]">
          <X size={14} />
        </button>
      </div>

      <div className="px-4 py-5 flex flex-col items-center text-center border-b border-[var(--border)]">
        <div className="w-20 h-20 rounded-2xl bg-[var(--bg-soft)] border border-[var(--border)] flex items-center justify-center mb-3">
          <Icon size={36} className={info.color} />
        </div>
        <p className="text-[15px] font-medium text-[var(--text)] break-words w-full">{item.name}</p>
        <button
          onClick={() => onToggleStar(item)}
          className="mt-2 flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-amber-400 transition-colors"
        >
          <Star size={13} className={item.starred ? "text-amber-400 fill-amber-400" : ""} />
          {item.starred ? "Starred" : "Add to starred"}
        </button>
      </div>

      <dl className="px-5 py-2 divide-y divide-[var(--border)]">
        <Row label="Type" value={item.type === "folder" ? "Folder" : info.label} />
        <Row label="Size" value={item.type === "folder" ? `${item.itemCount} items` : formatBytes(item.sizeBytes)} />
        <Row label="Location" value={item.parentId ? "Inside a folder" : "My Drive"} />
        <Row label="Source" value={sourceLabel(item.localPath)} />
        <Row label="Local path" value={item.localPath || "—"} mono />
        <Row label="Modified" value={`${formatDateTime(item.modifiedAt)} (${formatRelative(item.modifiedAt)})`} />
        <Row label="Created" value={formatDateTime(item.createdAt)} />
        <Row label="Storage root" value={item.storageRoot || "Notion Cloud"} />
        <Row label="Item ID" value={item.id} mono />
        <Row label="Sync status" value={item.archived ? "Archived" : "Synced"} />
      </dl>
    </aside>
  );
}
