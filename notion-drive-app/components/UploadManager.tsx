"use client";

import { AlertCircle, CheckCircle2, Loader2, RotateCcw, X } from "lucide-react";
import type { UploadTask } from "@/lib/types";
import { formatBytes, formatSpeed, formatEta } from "@/lib/format";

interface UploadManagerProps {
  jobs: UploadTask[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRetry: (id: string) => void;
  onCancel: (id: string) => void;
  onRemove: (id: string) => void;
  onClearFinished: () => void;
  activeCount: number;
}

export default function UploadManager({
  jobs, open, onOpenChange, onRetry, onCancel, onRemove, onClearFinished, activeCount,
}: UploadManagerProps) {
  const visible = jobs.filter((j) => j.status !== "done" || open);
  if (!open) {
    // Show a compact floating indicator when uploads are active.
    if (activeCount === 0) return null;
    return (
      <button
        onClick={() => onOpenChange(true)}
        className="fixed bottom-12 right-5 z-40 flex items-center gap-2 px-3.5 py-2 bg-[var(--bg-elevated)] border border-[var(--border)] rounded-xl shadow-xl text-xs text-[var(--text)]"
      >
        <Loader2 size={13} className="animate-spin text-blue-400" />
        {activeCount} uploading…
      </button>
    );
  }

  if (jobs.length === 0) return null;

  return (
    <div className="fixed bottom-12 right-5 z-50 w-80 bg-[var(--bg-elevated)] border border-[var(--border)] rounded-2xl shadow-2xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
        <span className="text-sm font-semibold text-[var(--text)]">
          Uploads {activeCount > 0 && <span className="text-blue-400">({activeCount} active)</span>}
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={onClearFinished}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-muted)] hover:text-[var(--text)]"
            title="Clear finished"
          >
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="max-h-72 overflow-y-auto divide-y divide-[var(--border)]">
        {visible.map((job) => (
          <div key={job.id} className="px-4 py-3">
            <div className="flex items-center gap-2.5">
              {job.status === "done" && <CheckCircle2 size={14} className="text-green-400 shrink-0" />}
              {job.status === "error" && <AlertCircle size={14} className="text-red-400 shrink-0" />}
              {(job.status === "pending" || job.status === "uploading") && (
                <Loader2 size={14} className="text-blue-400 shrink-0 animate-spin" />
              )}
              <p className="text-[12px] text-[var(--text)] truncate flex-1">{job.name}</p>
              {job.status === "error" ? (
                <button
                  onClick={() => onRetry(job.id)}
                  className="text-[11px] text-blue-400 hover:text-blue-300 shrink-0 flex items-center gap-1"
                >
                  <RotateCcw size={11} /> Retry
                </button>
              ) : job.status === "done" ? (
                <button
                  onClick={() => onRemove(job.id)}
                  className="text-[var(--text-muted)] hover:text-[var(--text)] shrink-0"
                >
                  <X size={12} />
                </button>
              ) : (
                <button
                  onClick={() => onCancel(job.id)}
                  className="text-[var(--text-muted)] hover:text-red-400 shrink-0"
                  title="Cancel"
                >
                  <X size={12} />
                </button>
              )}
            </div>
            {(job.status === "uploading" || job.status === "pending") && (
              <>
                <div className="mt-2 h-1 bg-[var(--bg-soft)] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all duration-300"
                    style={{ width: `${job.progress}%` }}
                  />
                </div>
                <div className="mt-1 flex justify-between text-[10px] text-[var(--text-muted)]">
                  <span>
                    {formatBytes((job.size * job.progress) / 100)} / {formatBytes(job.size)}
                    {job.speedBytesPerSec ? ` · ${formatSpeed(job.speedBytesPerSec)}` : ""}
                  </span>
                  <span>{formatEta(job.etaSeconds ?? 0)}</span>
                </div>
              </>
            )}
            {job.status === "error" && (
              <p className="mt-1 text-[11px] text-red-400 truncate">{job.error ?? "Upload failed"}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
