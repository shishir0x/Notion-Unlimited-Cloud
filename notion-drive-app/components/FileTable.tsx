"use client";
import { useState } from "react";
import { Folder, File, Star, ExternalLink, ArrowUpDown } from "lucide-react";
import type { DriveItem } from "./FileCard";

interface FileTableProps {
  items: DriveItem[];
  selected: Set<string>;
  onSelect: (id: string, multi: boolean) => void;
  onOpen: (item: DriveItem) => void;
  onPreview: (item: DriveItem) => void;
}

type SortKey = "name" | "sizeMb" | "modifiedAt" | "fileType";

export default function FileTable({ items, selected, onSelect, onOpen, onPreview }: FileTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const sorted = [...items].sort((a, b) => {
    let av: string | number = a[sortKey] as string | number;
    let bv: string | number = b[sortKey] as string | number;
    if (typeof av === "string") av = av.toLowerCase();
    if (typeof bv === "string") bv = bv.toLowerCase();
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sortDir === "asc" ? cmp : -cmp;
  });

  const SortBtn = ({ col, label }: { col: SortKey; label: string }) => (
    <button
      onClick={() => handleSort(col)}
      className="flex items-center gap-1 text-gray-400 hover:text-white transition-colors"
    >
      {label}
      <ArrowUpDown size={12} className={sortKey === col ? "text-blue-400" : "opacity-30"} />
    </button>
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/5">
            <th className="w-10 px-4 py-3">
              <input
                type="checkbox"
                className="accent-blue-500 w-4 h-4"
                checked={selected.size === items.length && items.length > 0}
                onChange={(e) => {
                  if (e.target.checked) items.forEach((i) => onSelect(i.id, true));
                  else items.forEach((i) => onSelect(i.id, true)); // deselect via toggle
                }}
              />
            </th>
            <th className="px-4 py-3 text-left font-medium"><SortBtn col="name" label="Name" /></th>
            <th className="px-4 py-3 text-left font-medium hidden md:table-cell"><SortBtn col="fileType" label="Type" /></th>
            <th className="px-4 py-3 text-left font-medium hidden sm:table-cell"><SortBtn col="sizeMb" label="Size" /></th>
            <th className="px-4 py-3 text-left font-medium hidden lg:table-cell"><SortBtn col="modifiedAt" label="Modified" /></th>
            <th className="px-4 py-3" />
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.03]">
          {sorted.map((item) => (
            <tr
              key={item.id}
              onClick={(e) => {
                if (e.ctrlKey || e.metaKey || e.shiftKey) {
                  onSelect(item.id, true);
                } else if (item.type === "folder") {
                  onOpen(item);
                } else {
                  onSelect(item.id, false);
                }
              }}
              onDoubleClick={() => onOpen(item)}
              className={`group transition-colors cursor-pointer ${
                selected.has(item.id)
                  ? "bg-blue-500/10"
                  : "hover:bg-white/[0.03]"
              }`}
            >
              <td className="px-4 py-2.5">
                <input
                  type="checkbox"
                  className="accent-blue-500 w-4 h-4"
                  checked={selected.has(item.id)}
                  onChange={(e) => { e.stopPropagation(); onSelect(item.id, true); }}
                />
              </td>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-2.5">
                  {item.type === "folder" ? (
                    item.name.includes("(C:)") ? (
                      <span className="text-base shrink-0">💽</span>
                    ) : item.name.includes("(D:)") ? (
                      <span className="text-base shrink-0">💾</span>
                    ) : item.name.toLowerCase().includes("phone") ? (
                      <span className="text-base shrink-0">📱</span>
                    ) : item.name.toLowerCase().includes("sd card") ? (
                      <span className="text-base shrink-0">🗃️</span>
                    ) : (
                      <Folder size={16} className="text-amber-400 shrink-0" fill="currentColor" />
                    )
                  ) : (
                    <File size={16} className="text-gray-500 shrink-0" />
                  )}
                  <span className="text-white/90 font-medium truncate max-w-[260px]">{item.name}</span>
                  {item.starred && <Star size={11} className="text-amber-400 fill-amber-400 shrink-0" />}
                </div>
              </td>
              <td className="px-4 py-2.5 text-gray-500 hidden md:table-cell">{item.fileType}</td>
              <td className="px-4 py-2.5 text-gray-500 hidden sm:table-cell">
                {item.type === "folder" ? "—" : `${item.sizeMb.toFixed(1)} MB`}
              </td>
              <td className="px-4 py-2.5 text-gray-500 hidden lg:table-cell text-[12px]">
                {new Date(item.modifiedAt).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })}
              </td>
              <td className="px-4 py-2.5">
                <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity justify-end">
                  <button onClick={(e) => { e.stopPropagation(); onPreview(item); }} className="p-1.5 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white">
                    <File size={13} />
                  </button>
                  <a href={item.notionUrl} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="p-1.5 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white">
                    <ExternalLink size={13} />
                  </a>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
