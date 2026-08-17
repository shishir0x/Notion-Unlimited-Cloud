"use client";
import { Folder, File, Star, ExternalLink, ArrowUpDown, Download, Eye, Trash2 } from "lucide-react";
import type { DriveItem, SortKey, SortDir } from "@/lib/types";

interface FileTableProps {
  items: DriveItem[];
  selected: Set<string>;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  onSelect: (id: string, multi: boolean) => void;
  onSelectRange: (id: string) => void;
  onOpen: (item: DriveItem) => void;
  onPreview: (item: DriveItem) => void;
  onContextMenu: (e: React.MouseEvent, item: DriveItem) => void;
  onDragStart: (item: DriveItem) => void;
  onDragOver: (item: DriveItem) => void;
  onDropOn: (folderId: string) => void;
  onAction: (action: string, item: DriveItem) => void;
}

const COLUMNS: { key: SortKey; label: string; className: string }[] = [
  { key: "name", label: "Name", className: "" },
  { key: "type", label: "Type", className: "hidden md:table-cell" },
  { key: "size", label: "Size", className: "hidden sm:table-cell" },
  { key: "mtime", label: "Modified", className: "hidden lg:table-cell" },
];

export default function FileTable({
  items, selected, sortKey, sortDir, onSort,
  onSelect, onSelectRange, onOpen, onPreview,
  onContextMenu, onDragStart, onDragOver, onDropOn, onAction,
}: FileTableProps) {
  const SortBtn = ({ col, label }: { col: SortKey; label: string }) => (
    <button
      onClick={() => onSort(col)}
      className="flex items-center gap-1 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors uppercase text-[11px] tracking-wide"
    >
      {label}
      {sortKey === col ? (
        <span className="text-blue-500 text-[10px]">{sortDir === "asc" ? "↑" : "↓"}</span>
      ) : (
        <ArrowUpDown size={11} className="opacity-30" />
      )}
    </button>
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--border)]">
            <th className="w-10 px-4 py-3">
              <input
                type="checkbox"
                className="accent-blue-500 w-4 h-4"
                checked={selected.size === items.length && items.length > 0}
                onChange={(e) => {
                  items.forEach((i) => {
                    if (e.target.checked && !selected.has(i.id)) onSelect(i.id, true);
                    if (!e.target.checked && selected.has(i.id)) onSelect(i.id, true);
                  });
                }}
                aria-label="Select all"
              />
            </th>
            {COLUMNS.map((c) => (
              <th key={c.key} className={`px-4 py-3 text-left font-medium ${c.className}`}>
                <SortBtn col={c.key} label={c.label} />
              </th>
            ))}
            <th className="px-4 py-3" />
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {items.map((item) => (
            <tr
              key={item.id}
              draggable={item.type === "file"}
              onDragStart={(e) => { e.stopPropagation(); onDragStart(item); }}
              onDragOver={(e) => { if (item.type === "folder") { e.preventDefault(); e.stopPropagation(); onDragOver(item); } }}
              onDrop={(e) => { if (item.type === "folder") { e.preventDefault(); e.stopPropagation(); onDropOn(item.id); } }}
              onClick={(e) => {
                if (e.shiftKey) onSelectRange(item.id);
                else onSelect(item.id, e.ctrlKey || e.metaKey);
              }}
              onDoubleClick={() => onOpen(item)}
              onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContextMenu(e, item); }}
              className={`group transition-colors cursor-pointer ${
                selected.has(item.id) ? "bg-blue-500/10" : "hover:bg-[var(--bg-hover)]"
              }`}
            >
              <td className="px-4 py-2.5">
                <input
                  type="checkbox"
                  className="accent-blue-500 w-4 h-4"
                  checked={selected.has(item.id)}
                  onChange={(e) => { e.stopPropagation(); onSelect(item.id, true); }}
                  aria-label={`Select ${item.name}`}
                />
              </td>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2.5">
                  {item.type === "folder" ? (
                    <Folder size={16} className="text-amber-400 shrink-0" fill="currentColor" />
                  ) : (
                    <File size={16} className="text-[var(--text-muted)] shrink-0" />
                  )}
                  <span className="text-[var(--text)] font-medium truncate max-w-[260px]">{item.name}</span>
                  {item.starred && <Star size={11} className="text-amber-400 fill-amber-400 shrink-0" />}
                </div>
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)] hidden md:table-cell">{item.fileType}</td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)] hidden sm:table-cell">
                {item.type === "folder" ? (item.itemCount > 0 ? `${item.itemCount} items` : "—") : `${item.sizeMb.toFixed(1)} MB`}
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)] hidden lg:table-cell text-[12px]">
                {new Date(item.modifiedAt).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })}
              </td>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity justify-end">
                  <button
                    onClick={(e) => { e.stopPropagation(); onPreview(item); }}
                    className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]"
                    title="Preview"
                  >
                    <Eye size={13} />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onAction("star", item); }}
                    className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]"
                    title={item.starred ? "Unstar" : "Star"}
                  >
                    <Star size={13} className={item.starred ? "text-amber-400 fill-amber-400" : ""} />
                  </button>
                  <a
                    href={`/api/download?id=${item.id}&local_path=${encodeURIComponent(item.localPath)}`}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]"
                    title="Download"
                  >
                    <Download size={13} />
                  </a>
                  <a
                    href={item.notionUrl}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]"
                    title="Open in Notion"
                  >
                    <ExternalLink size={13} />
                  </a>
                  <button
                    onClick={(e) => { e.stopPropagation(); onAction("delete", item); }}
                    className="p-1.5 rounded-lg hover:bg-red-500/10 text-[var(--text-muted)] hover:text-red-400"
                    title="Delete"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
