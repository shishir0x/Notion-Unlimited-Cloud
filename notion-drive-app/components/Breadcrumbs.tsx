"use client";
import { ChevronRight, HardDrive } from "lucide-react";
import type { Breadcrumb } from "@/lib/types";

interface BreadcrumbsProps {
  crumbs?: Breadcrumb[];
  onNavigate: (id: string | null) => void;
}

export default function Breadcrumbs({ crumbs = [], onNavigate }: BreadcrumbsProps) {
  const safeCrumbs = crumbs ?? [];
  return (
    <nav className="flex items-center gap-1 text-sm overflow-x-auto no-scrollbar" aria-label="Breadcrumbs">
      <button
        onClick={() => onNavigate(null)}
        className="flex items-center gap-1.5 text-[var(--text-secondary)] hover:text-[var(--text)] transition-colors shrink-0 py-1 px-2 rounded-lg hover:bg-[var(--bg-hover)]"
      >
        <HardDrive size={13} className="text-blue-500" />
        <span>My Drive</span>
      </button>
      {safeCrumbs.map((crumb, i) => (
        <span key={`${crumb?.id ?? "root"}-${i}`} className="flex items-center gap-1 shrink-0">
          <ChevronRight size={13} className="text-[var(--text-muted)]" />
          {i === safeCrumbs.length - 1 ? (
            <span className="text-[var(--text)] py-1 px-2 max-w-[180px] truncate font-medium">{crumb?.name}</span>
          ) : (
            <button
              onClick={() => onNavigate(crumb?.id ?? null)}
              className="text-[var(--text-secondary)] hover:text-[var(--text)] transition-colors py-1 px-2 rounded-lg hover:bg-[var(--bg-hover)] max-w-[180px] truncate"
            >
              {crumb?.name}
            </button>
          )}
        </span>
      ))}
    </nav>
  );
}
