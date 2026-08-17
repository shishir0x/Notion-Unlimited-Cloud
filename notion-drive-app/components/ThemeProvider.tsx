"use client";

import { useEffect, useState } from "react";
import type { ThemePreference } from "@/lib/types";

const STORAGE_KEY = "nd-theme";

function applyTheme(pref: ThemePreference) {
  const root = document.documentElement;
  const isDark = pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.classList.toggle("dark", isDark);
  root.classList.toggle("light", !isDark);
  root.style.colorScheme = isDark ? "dark" : "light";
}

export function useTheme(): [ThemePreference, (p: ThemePreference) => void] {
  const [pref, setPref] = useState<ThemePreference>("system");

  // Hydrate from localStorage during render (React's documented
  // "adjust state during render" pattern) instead of in an effect.
  const [hydrated, setHydrated] = useState(false);
  if (!hydrated && typeof window !== "undefined") {
    const stored = (localStorage.getItem(STORAGE_KEY) as ThemePreference) || "system";
    setHydrated(true);
    setPref(stored);
  }

  useEffect(() => {
    applyTheme(pref);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if ((localStorage.getItem(STORAGE_KEY) as ThemePreference) === "system") applyTheme("system");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  const setTheme = (p: ThemePreference) => {
    setPref(p);
    localStorage.setItem(STORAGE_KEY, p);
    applyTheme(p);
  };

  return [pref, setTheme];
}

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const stored = (localStorage.getItem(STORAGE_KEY) as ThemePreference) || "system";
    applyTheme(stored);
  }, []);
  return <>{children}</>;
}
