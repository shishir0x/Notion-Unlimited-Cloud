"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "./ThemeProvider";
import type { ThemePreference } from "@/lib/types";

const OPTIONS: { value: ThemePreference; icon: typeof Sun; label: string }[] = [
  { value: "light", icon: Sun, label: "Light" },
  { value: "dark", icon: Moon, label: "Dark" },
  { value: "system", icon: Monitor, label: "System" },
];

export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [pref, setTheme] = useTheme();

  if (compact) {
    const current = OPTIONS.find((o) => o.value === pref) ?? OPTIONS[2];
    const Icon = current.icon;
    return (
      <button
        onClick={() => {
          const next = OPTIONS[(OPTIONS.findIndex((o) => o.value === pref) + 1) % OPTIONS.length];
          setTheme(next.value);
        }}
        className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text)] transition-colors"
        title={`Theme: ${current.label} (click to change)`}
        aria-label={`Theme: ${current.label}`}
      >
        <Icon size={15} />
      </button>
    );
  }

  return (
    <div className="flex items-center bg-[var(--bg-soft)] border border-[var(--border)] rounded-lg p-1">
      {OPTIONS.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          onClick={() => setTheme(value)}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs transition-colors ${
            pref === value
              ? "bg-[var(--bg-hover)] text-[var(--text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
          }`}
          title={label}
          aria-label={`${label} theme`}
        >
          <Icon size={13} />
          <span className="hidden sm:inline">{label}</span>
        </button>
      ))}
    </div>
  );
}
