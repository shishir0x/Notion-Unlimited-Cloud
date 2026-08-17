"use client";

import { useEffect, useState } from "react";
import { FolderPlus, Pencil, X } from "lucide-react";

interface NameDialogProps {
  open: boolean;
  title: string;
  label: string;
  initialValue?: string;
  submitLabel?: string;
  onClose: () => void;
  onSubmit: (value: string) => Promise<void> | void;
}

export default function NameDialog({
  open, title, label, initialValue = "", submitLabel = "Save", onClose, onSubmit,
}: NameDialogProps) {
  const [value, setValue] = useState(initialValue);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Reset the field whenever the dialog opens (adjust state during render).
  const [prevOpen, setPrevOpen] = useState(open);
  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setValue(initialValue);
      setError("");
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const Icon = submitLabel.includes("Create") ? FolderPlus : Pencil;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const v = value.trim();
    if (!v) {
      setError("Name is required");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit(v);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form
        onSubmit={submit}
        className="w-full max-w-sm bg-[var(--bg-elevated)] border border-[var(--border)] rounded-2xl shadow-2xl p-5"
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-[var(--text)] flex items-center gap-2">
            <Icon size={15} className="text-[var(--text-muted)]" /> {title}
          </h2>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)]">
            <X size={14} />
          </button>
        </div>

        <label className="block text-xs text-[var(--text-muted)] mb-1.5">{label}</label>
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onFocus={(e) => {
            const dot = e.target.value.lastIndexOf(".");
            if (dot > 0) e.target.setSelectionRange(0, dot);
          }}
          className="w-full px-3 py-2.5 bg-[var(--bg-inset)] border border-[var(--border)] rounded-xl text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
        />
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}

        <div className="flex justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="px-4 py-2 rounded-xl text-sm font-semibold bg-blue-600 hover:bg-blue-500 text-white transition-colors disabled:opacity-50"
          >
            {busy ? "Saving…" : submitLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
