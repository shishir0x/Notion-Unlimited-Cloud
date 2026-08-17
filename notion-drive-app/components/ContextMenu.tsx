"use client";

import { useEffect, useRef } from "react";
import type { LucideIcon } from "lucide-react";

export interface MenuItem {
  label?: string;
  icon?: LucideIcon;
  onClick?: () => void;
  danger?: boolean;
  disabled?: boolean;
  separator?: boolean;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}

export default function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onScroll = () => onClose();
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  // Keep the menu inside the viewport.
  const menuWidth = 200;
  const menuHeight = items.length * 36 + 12;
  const left = Math.min(x, window.innerWidth - menuWidth - 8);
  const top = Math.min(y, window.innerHeight - menuHeight - 8);

  return (
    <div
      ref={ref}
      role="menu"
      className="fixed z-[90] bg-[var(--bg-elevated)] border border-[var(--border)] rounded-xl shadow-2xl py-1.5 min-w-[200px]"
      style={{ left: Math.max(8, left), top: Math.max(8, top) }}
      onMouseDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item, i) =>
        item.separator ? (
          <div key={`sep-${i}`} className="my-1 h-px bg-[var(--border)]" />
        ) : (
          <button
            key={`${item.label}-${i}`}
            role="menuitem"
            disabled={item.disabled}
            onClick={() => {
              onClose();
              item.onClick?.();
            }}
            className={`w-full flex items-center gap-2.5 px-3 py-2 text-[13px] transition-colors disabled:opacity-40 ${
              item.danger
                ? "text-red-400 hover:bg-red-500/10"
                : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text)]"
            }`}
          >
            {item.icon && <item.icon size={14} className={item.danger ? "" : "text-[var(--text-muted)]"} />}
            {item.label}
          </button>
        ),
      )}
    </div>
  );
}
