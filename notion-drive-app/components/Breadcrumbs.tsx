"use client";
import { ChevronRight, HardDrive } from "lucide-react";

export interface Crumb {
  id: string;
  name: string;
}

interface BreadcrumbsProps {
  crumbs: Crumb[];
  onNavigate: (id: string | null) => void;
}

export default function Breadcrumbs({ crumbs, onNavigate }: BreadcrumbsProps) {
  return (
    <nav className="flex items-center gap-1 text-sm overflow-x-auto no-scrollbar">
      <button
        onClick={() => onNavigate(null)}
        className="flex items-center gap-1.5 text-gray-400 hover:text-white transition-colors shrink-0 py-1 px-2 rounded-lg hover:bg-white/5"
      >
        <HardDrive size={13} className="text-blue-400" />
        <span>My Drive</span>
      </button>
      {crumbs.map((crumb) => (
        <span key={crumb.id} className="flex items-center gap-1 shrink-0">
          <ChevronRight size={13} className="text-gray-600" />
          <button
            onClick={() => onNavigate(crumb.id)}
            className="text-gray-400 hover:text-white transition-colors py-1 px-2 rounded-lg hover:bg-white/5 max-w-[180px] truncate"
          >
            {crumb.name}
          </button>
        </span>
      ))}
    </nav>
  );
}
