"use client";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { motion } from "framer-motion";
import {
  RefreshCw, Terminal, CheckCircle2,
  Sparkles, Copy, Trash2,
  Zap, ArrowUpRight, Check, Activity, Clock, FileText, CheckCircle
} from "lucide-react";

interface SyncLog {
  id: string;
  level: "info" | "success" | "warn" | "error";
  message: string;
  path?: string;
  timestamp: string;
}

interface UploadedFileItem {
  id: string;
  name: string;
  extension: string;
  sizeMb: number;
  modifiedAt: string;
  notionUrl: string;
  description: string;
}

interface PendingFileItem {
  name: string;
  path: string;
  size: number;
  status: string;
}

export default function SyncView({
  onRefreshDrive,
}: {
  onRefreshDrive?: () => void;
}) {
  const [syncing, setSyncing] = useState(false);
  const [target, setTarget] = useState("all");
  const [percent, setPercent] = useState(100);
  const [currentAction, setCurrentAction] = useState("Idle · Terminal Sync Engine Connected");
  const [totalFiles, setTotalFiles] = useState(0);
  const [totalMb, setTotalMb] = useState(0);
  const [lastSyncedAt, setLastSyncedAt] = useState<string>("");
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileItem[]>([]);
  const [pendingFiles, setPendingFiles] = useState<PendingFileItem[]>([]);
  const [copied, setCopied] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);

  const consoleEndRef = useRef<HTMLDivElement>(null);

  const fetchSyncStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/sync");
      if (!res.ok) return;
      const data = await res.json();
      setTotalFiles(data.totalFiles || 0);
      setTotalMb(data.totalMb || 0);
      setLastSyncedAt(data.lastSyncedAt || "");
      if (data.logs && Array.isArray(data.logs)) {
        setLogs(data.logs);
      }
      if (data.uploadedFiles && Array.isArray(data.uploadedFiles)) {
        setUploadedFiles(data.uploadedFiles);
      }
      if (data.pendingFiles && Array.isArray(data.pendingFiles)) {
        setPendingFiles(data.pendingFiles);
      }
      if (data.isRunning !== undefined) {
        setSyncing(data.isRunning);
      }
      if (data.currentAction) {
        setCurrentAction(data.currentAction);
      }
      if (data.percent !== undefined) {
        setPercent(data.percent);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchSyncStatus();
    const interval = setInterval(fetchSyncStatus, 2000);
    return () => clearInterval(interval);
  }, [fetchSyncStatus]);

  useEffect(() => {
    if (autoScroll && consoleEndRef.current) {
      consoleEndRef.current.scrollTop = consoleEndRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  // Sort completed uploads: Newest first
  const sortedUploadedFiles = useMemo(() => {
    return [...uploadedFiles].sort((a, b) => {
      const timeA = new Date(a.modifiedAt).getTime() || 0;
      const timeB = new Date(b.modifiedAt).getTime() || 0;
      return timeB - timeA;
    });
  }, [uploadedFiles]);

  // Sort pending queue: Stable FIFO queue order
  const sortedPendingFiles = useMemo(() => {
    return [...pendingFiles];
  }, [pendingFiles]);

  const handleStartSync = async () => {
    if (syncing) return;
    setSyncing(true);
    setPercent(15);
    setCurrentAction(`Scanning ${target === "all" ? "all connected devices" : target}...`);

    try {
      const res = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start", target }),
      });
      const data = await res.json();
      if (res.status === 200 && data.success) {
        setPercent(100);
        setCurrentAction("Sync completed successfully");
        if (data.logs) setLogs(data.logs);
        if (data.totalFiles) setTotalFiles(data.totalFiles);
        if (data.totalMb) setTotalMb(data.totalMb);
        onRefreshDrive?.();
      }
    } catch (err: any) {
      setCurrentAction(`Sync error: ${err.message || "Failed to execute sync"}`);
    } finally {
      setSyncing(false);
      fetchSyncStatus();
    }
  };

  const handleCopyLogs = () => {
    const text = logs.map((l) => `[${l.timestamp}] [${l.level.toUpperCase()}] ${l.message} ${l.path ? `(${l.path})` : ""}`).join("\n");
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleClearLogs = () => {
    setLogs([
      {
        id: "cleared",
        level: "info",
        message: "Console output cleared.",
        timestamp: new Date().toLocaleTimeString(),
      },
    ]);
  };

  return (
    <div className="flex-1 overflow-y-auto bg-[#0d0f11] text-white p-6 sm:p-8 space-y-6">
      {/* ── Top Header Bar ────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white/[0.02] border border-white/[0.06] rounded-2xl p-6 backdrop-blur-xl">
        <div className="flex items-center gap-3.5">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/20 text-white">
            <Activity size={24} className={syncing ? "animate-pulse" : ""} />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-lg sm:text-xl font-bold tracking-tight text-white">Sync & Activity Center</h1>
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                Live Connected
              </span>
            </div>
            <p className="text-gray-400 text-xs mt-0.5">
              Terminal Engine Telemetry & Notion Database Orchestrator
            </p>
          </div>
        </div>

        {/* Sync Controls */}
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={syncing}
            className="bg-black/40 border border-white/10 text-gray-300 text-xs rounded-xl px-3.5 py-2.5 outline-none focus:border-blue-500 transition-colors"
          >
            <option value="all">⚡ All Devices & Cloud</option>
            <option value="phone">📱 Phone (Full Storage Scan)</option>
            <option value="sdcard">🗃️ SD Card</option>
            <option value="disk-c">💽 Local Disk (C:)</option>
            <option value="disk-d">💾 Local Disk (D:)</option>
          </select>

          <button
            onClick={handleStartSync}
            disabled={syncing}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-semibold rounded-xl shadow-lg shadow-blue-600/25 transition-all active:scale-[0.98]"
          >
            <RefreshCw size={14} className={syncing ? "animate-spin" : ""} />
            {syncing ? "Syncing Cloud..." : "Start Sync"}
          </button>
        </div>
      </div>

      {/* ── KPI Metric Cards ──────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-4.5">
          <span className="text-[11px] text-gray-500 font-medium block">Total Synced Files</span>
          <span className="text-xl sm:text-2xl font-bold text-white mt-1 block">
            {totalFiles.toLocaleString()}
          </span>
          <span className="text-[11px] text-blue-400 flex items-center gap-1 mt-1">
            <Sparkles size={11} /> 100% Notion Cloud
          </span>
        </div>

        <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-4.5">
          <span className="text-[11px] text-gray-500 font-medium block">Total Cloud Storage</span>
          <span className="text-xl sm:text-2xl font-bold text-white mt-1 block">
            {(totalMb / 1024).toFixed(2)} GB
          </span>
          <span className="text-[11px] text-emerald-400 flex items-center gap-1 mt-1">
            <Zap size={11} /> Unlimited Storage
          </span>
        </div>

        <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-4.5">
          <span className="text-[11px] text-gray-500 font-medium block">Sync Engine State</span>
          <span className="text-xl sm:text-2xl font-bold text-emerald-400 mt-1 block flex items-center gap-2">
            <CheckCircle2 size={20} /> Active
          </span>
          <span className="text-[11px] text-gray-400 mt-1 block truncate">
            {syncing ? "Processing Sync..." : "Terminal Connected"}
          </span>
        </div>

        <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-4.5">
          <span className="text-[11px] text-gray-500 font-medium block">Last Cloud Sync</span>
          <span className="text-sm font-semibold text-white mt-1 block truncate">
            {lastSyncedAt ? new Date(lastSyncedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "Just now"}
          </span>
          <span className="text-[11px] text-gray-500 mt-1 block">
            {lastSyncedAt ? new Date(lastSyncedAt).toLocaleDateString([], { month: "short", day: "numeric" }) : "Today"}
          </span>
        </div>
      </div>

      {/* ── Active Progress Indicator ─────────────────────────────────── */}
      <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl p-5 space-y-3">
        <div className="flex items-center justify-between text-xs">
          <span className="text-gray-300 font-medium flex items-center gap-2">
            <RefreshCw size={13} className={syncing ? "animate-spin text-blue-400" : "text-gray-500"} />
            {currentAction}
          </span>
          <span className="text-blue-400 font-mono font-bold">{percent}%</span>
        </div>
        <div className="h-2 w-full bg-white/[0.05] rounded-full overflow-hidden">
          <motion.div
            className="h-full bg-gradient-to-r from-blue-500 via-indigo-500 to-emerald-400 rounded-full"
            initial={{ width: "0%" }}
            animate={{ width: `${percent}%` }}
            transition={{ duration: 0.3 }}
          />
        </div>
      </div>

      {/* ── Real-Time Terminal Console ────────────────────────────────── */}
      <div className="bg-[#0a0b0d] border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        {/* Terminal Header Bar */}
        <div className="flex items-center justify-between px-4 py-3 bg-white/[0.03] border-b border-white/[0.06]">
          <div className="flex items-center gap-2">
            <Terminal size={14} className="text-blue-400" />
            <span className="text-xs font-mono font-semibold text-gray-300">Live Terminal Sync Telemetry</span>
            <span className="text-[10px] text-gray-500 font-mono">({logs.length} events logged)</span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoScroll((v) => !v)}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-mono transition-colors ${
                autoScroll ? "bg-blue-500/20 text-blue-300" : "bg-white/5 text-gray-400"
              }`}
            >
              Auto-Scroll {autoScroll ? "ON" : "OFF"}
            </button>
            <button
              onClick={handleCopyLogs}
              className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
              title="Copy Terminal Logs"
            >
              {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
            </button>
            <button
              onClick={handleClearLogs}
              className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
              title="Clear Console Output"
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        {/* Terminal Output */}
        <div
          ref={consoleEndRef}
          className="p-4 font-mono text-[12px] leading-relaxed max-h-72 overflow-y-auto space-y-1.5 select-text bg-black/60"
        >
          {logs.map((log) => {
            let color = "text-gray-300";
            if (log.level === "success") color = "text-emerald-400";
            if (log.level === "warn") color = "text-amber-400";
            if (log.level === "error") color = "text-rose-400";
            if (log.level === "info") color = "text-cyan-400";

            return (
              <div key={log.id} className="flex items-start gap-2 hover:bg-white/[0.02] px-1 py-0.5 rounded">
                <span className="text-gray-600 select-none text-[11px] shrink-0">[{log.timestamp}]</span>
                <span className={`font-bold text-[11px] shrink-0 uppercase ${color}`}>
                  [{log.level}]
                </span>
                <span className="text-gray-300 break-all">{log.message}</span>
                {log.path && (
                  <span className="text-gray-500 text-[11px] ml-auto shrink-0 truncate max-w-xs">
                    {log.path}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 2-Column Side-by-Side: Pending Queue (LEFT) vs Finished / Uploaded (RIGHT) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* LEFT COLUMN: Pending Upload Queue */}
        <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl overflow-hidden flex flex-col h-[460px] select-none">
          {/* Header */}
          <div className="px-5 py-4 border-b border-white/[0.06] bg-white/[0.01] flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              <Clock size={16} className="text-amber-400" />
              <h2 className="text-sm font-semibold text-white">Pending Upload Queue</h2>
            </div>
            <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
              {sortedPendingFiles.length} {sortedPendingFiles.length === 1 ? "file" : "files"} waiting
            </span>
          </div>

          {/* List Content with stabilized scrolling */}
          <div className="flex-1 overflow-y-auto p-2.5 space-y-1.5 overscroll-contain">
            {sortedPendingFiles.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center p-8 text-center text-gray-500 space-y-2">
                <CheckCircle size={36} className="text-emerald-500/40" />
                <p className="text-xs font-medium text-gray-300">Upload Queue is Clear</p>
                <p className="text-[11px] text-gray-500 max-w-xs">
                  All local and phone files are completely synchronized with Notion Cloud.
                </p>
              </div>
            ) : (
              sortedPendingFiles.map((item) => (
                <div
                  key={item.path || item.name}
                  className="flex items-center justify-between gap-3 p-3 rounded-xl bg-white/[0.015] hover:bg-white/[0.03] border border-white/[0.03] transition-colors min-h-[58px]"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-xs text-white truncate max-w-[220px]">
                        {item.name}
                      </span>
                      <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20 shrink-0">
                        {item.status || "Queued"}
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-500 font-mono truncate mt-0.5">
                      {item.path}
                    </p>
                  </div>
                  <div className="text-right shrink-0">
                    <span className="text-xs font-mono text-gray-400 font-medium block">
                      {(item.size / (1024 * 1024)).toFixed(2)} MB
                    </span>
                    <span className="text-[10px] text-gray-600 block">Pending</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* RIGHT COLUMN: Finished / Synced to Notion */}
        <div className="bg-white/[0.02] border border-white/[0.06] rounded-2xl overflow-hidden flex flex-col h-[460px] select-none">
          {/* Header */}
          <div className="px-5 py-4 border-b border-white/[0.06] bg-white/[0.01] flex items-center justify-between shrink-0">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={16} className="text-emerald-400" />
              <h2 className="text-sm font-semibold text-white">Finished & Uploaded to Cloud</h2>
            </div>
            <span className="px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              {sortedUploadedFiles.length} {sortedUploadedFiles.length === 1 ? "file" : "files"} synced
            </span>
          </div>

          {/* List Content with stabilized scrolling */}
          <div className="flex-1 overflow-y-auto p-2.5 space-y-1.5 overscroll-contain">
            {sortedUploadedFiles.length === 0 ? (
              <div className="h-full flex flex-col items-center justify-center p-8 text-center text-gray-500 space-y-2">
                <FileText size={36} className="text-gray-600" />
                <p className="text-xs font-medium text-gray-300">No Synced Files Yet</p>
                <p className="text-[11px] text-gray-500 max-w-xs">
                  Run a sync to upload files from your local storage or connected phone to Notion.
                </p>
              </div>
            ) : (
              sortedUploadedFiles.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between gap-3 p-3 rounded-xl bg-white/[0.015] hover:bg-white/[0.03] border border-white/[0.03] transition-colors min-h-[58px]"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-xs text-white truncate max-w-[220px]">
                        {item.name}
                      </span>
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 shrink-0">
                        Synced
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-500 font-mono truncate mt-0.5">
                      {item.description || "Cloud Root"}
                    </p>
                  </div>

                  <div className="flex items-center gap-3 shrink-0">
                    <div className="text-right">
                      <span className="text-xs font-mono text-gray-300 font-medium block">
                        {item.sizeMb.toFixed(2)} MB
                      </span>
                      <span className="text-[10px] text-gray-500 block">
                        {new Date(item.modifiedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </div>
                    {item.notionUrl && (
                      <a
                        href={item.notionUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1.5 rounded-lg bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 hover:text-blue-300 transition-colors"
                        title="Open in Notion"
                      >
                        <ArrowUpRight size={14} />
                      </a>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
