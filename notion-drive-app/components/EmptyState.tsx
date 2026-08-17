"use client";

import { CloudOff, FileQuestion, FolderOpen, RefreshCw, TriangleAlert } from "lucide-react";

interface StateProps {
  onRetry?: () => void;
  message?: string;
}

export function EmptyFolder({ message = "This folder is empty" }: { message?: string }) {
  return (
    <div className="flex-1 flex items-center justify-center py-24">
      <div className="text-center space-y-3 px-4">
        <div className="w-16 h-16 mx-auto rounded-2xl bg-[var(--bg-soft)] border border-[var(--border)] flex items-center justify-center">
          <FolderOpen size={26} className="text-[var(--text-muted)]" />
        </div>
        <p className="text-[var(--text-secondary)] text-sm">{message}</p>
        <p className="text-[var(--text-muted)] text-xs">Drop files here or use the New button to add content</p>
      </div>
    </div>
  );
}

export function EmptyResults({ query }: { query: string }) {
  return (
    <div className="flex-1 flex items-center justify-center py-24">
      <div className="text-center space-y-3 px-4">
        <div className="w-16 h-16 mx-auto rounded-2xl bg-[var(--bg-soft)] border border-[var(--border)] flex items-center justify-center">
          <FileQuestion size={26} className="text-[var(--text-muted)]" />
        </div>
        <p className="text-[var(--text-secondary)] text-sm">No results for &ldquo;{query}&rdquo;</p>
        <p className="text-[var(--text-muted)] text-xs">Try a different search term</p>
      </div>
    </div>
  );
}

export function OfflineState({ onRetry }: StateProps) {
  return (
    <div className="flex-1 flex items-center justify-center py-24">
      <div className="text-center space-y-4 px-4 max-w-md">
        <div className="w-16 h-16 mx-auto rounded-2xl bg-[var(--bg-soft)] border border-[var(--border)] flex items-center justify-center">
          <CloudOff size={26} className="text-[var(--text-muted)]" />
        </div>
        <div>
          <p className="text-[var(--text)] font-medium">Notion Drive is offline</p>
          <p className="text-[var(--text-muted)] text-sm mt-1">
            The storage service could not be reached. Make sure the Python backend is running
            (<code className="text-xs bg-[var(--bg-soft)] px-1.5 py-0.5 rounded">python notion_server.py</code>)
            and try again.
          </p>
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-2 px-4 py-2 bg-[var(--bg-soft)] hover:bg-[var(--bg-hover)] border border-[var(--border)] text-[var(--text)] rounded-xl text-sm font-medium transition-colors"
          >
            <RefreshCw size={14} /> Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }: StateProps) {
  return (
    <div className="flex-1 flex items-center justify-center py-24">
      <div className="text-center space-y-3 px-4">
        <div className="w-16 h-16 mx-auto rounded-2xl bg-[var(--bg-soft)] border border-[var(--border)] flex items-center justify-center">
          <TriangleAlert size={26} className="text-amber-400" />
        </div>
        <p className="text-[var(--text-secondary)] text-sm">{message ?? "Something went wrong"}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-soft)] hover:bg-[var(--bg-hover)] border border-[var(--border)] text-[var(--text)] rounded-lg text-xs transition-colors"
          >
            <RefreshCw size={12} /> Retry
          </button>
        )}
      </div>
    </div>
  );
}
