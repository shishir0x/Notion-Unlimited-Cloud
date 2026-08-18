"use client";
import { useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, X, CheckCircle, AlertCircle, Loader2 } from "lucide-react";

interface UploadJob {
  id: string;
  file: File;
  progress: number;
  status: "pending" | "uploading" | "done" | "error";
  error?: string;
}

interface UploadDropzoneProps {
  folderId: string | null;
  onUploadComplete: () => void;
}

export default function UploadDropzone({ folderId, onUploadComplete }: UploadDropzoneProps) {
  const [dragging, setDragging] = useState(false);
  const [jobs, setJobs] = useState<UploadJob[]>([]);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadFile = useCallback(async (file: File) => {
    const id = Math.random().toString(36).slice(2);
    setJobs((prev) => [...prev, { id, file, progress: 0, status: "pending" }]);
    setOpen(true);

    const fd = new FormData();
    fd.append("file", file);
    if (folderId) fd.append("folder_id", folderId);

    try {
      setJobs((prev) => prev.map((j) => j.id === id ? { ...j, status: "uploading", progress: 30 } : j));
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (data.success) {
        setJobs((prev) => prev.map((j) => j.id === id ? { ...j, status: "done", progress: 100 } : j));
        onUploadComplete();
      } else {
        setJobs((prev) => prev.map((j) => j.id === id ? { ...j, status: "error", error: data.error } : j));
      }
    } catch {
      setJobs((prev) => prev.map((j) => j.id === id ? { ...j, status: "error", error: "Network error" } : j));
    }
  }, [folderId, onUploadComplete]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    files.forEach(uploadFile);
  }, [uploadFile]);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    files.forEach(uploadFile);
    e.target.value = "";
  }, [uploadFile]);

  const pendingCount = jobs.filter((j) => j.status !== "done" && j.status !== "error").length;

  return (
    <>
      {/* Full-page drag overlay */}
      <div
        className="fixed inset-0 z-40 pointer-events-none"
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        style={{ pointerEvents: dragging ? "auto" : "none" }}
      >
        <AnimatePresence>
          {dragging && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-4 rounded-2xl border-2 border-dashed border-blue-500 bg-blue-500/10 backdrop-blur-sm flex items-center justify-center"
            >
              <div className="text-center space-y-3">
                <Upload size={48} className="mx-auto text-blue-400" />
                <p className="text-white text-xl font-semibold">Drop files to upload</p>
                <p className="text-blue-300 text-sm">Files will be added to Notion Drive</p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Hidden file input */}
      <input ref={inputRef} type="file" multiple className="hidden" onChange={handleFileInput} />

      {/* Upload trigger button */}
      <button
        onClick={() => inputRef.current?.click()}
        className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-sm font-semibold transition-colors shadow-lg shadow-blue-500/20"
      >
        <Upload size={15} />
        Upload
      </button>

      {/* Upload queue drawer */}
      <AnimatePresence>
        {open && jobs.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            className="fixed bottom-5 right-5 z-50 bg-[#1a1c1f] border border-white/10 rounded-2xl shadow-2xl w-80 overflow-hidden"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
              <span className="text-white text-sm font-semibold">
                Uploads {pendingCount > 0 && <span className="text-blue-400">({pendingCount} active)</span>}
              </span>
              <button onClick={() => { setOpen(false); setJobs([]); }} className="text-gray-400 hover:text-white">
                <X size={15} />
              </button>
            </div>
            <div className="max-h-64 overflow-y-auto divide-y divide-white/[0.04]">
              {jobs.map((job) => (
                <div key={job.id} className="px-4 py-3">
                  <div className="flex items-center gap-2.5">
                    {job.status === "done" && <CheckCircle size={14} className="text-green-400 shrink-0" />}
                    {job.status === "error" && <AlertCircle size={14} className="text-red-400 shrink-0" />}
                    {(job.status === "pending" || job.status === "uploading") && (
                      <Loader2 size={14} className="text-blue-400 shrink-0 animate-spin" />
                    )}
                    <p className="text-[12px] text-white/80 truncate flex-1">{job.file.name}</p>
                    <span className={`text-[11px] shrink-0 ${
                      job.status === "done" ? "text-green-400" :
                      job.status === "error" ? "text-red-400" : "text-gray-400"
                    }`}>
                      {job.status === "done" ? "Done" : job.status === "error" ? "Failed" : "Uploading"}
                    </span>
                  </div>
                  {(job.status === "uploading" || job.status === "pending") && (
                    <div className="mt-2 h-1 bg-white/10 rounded-full overflow-hidden">
                      <motion.div
                        className="h-full bg-blue-500 rounded-full"
                        initial={{ width: 0 }}
                        animate={{ width: `${job.progress}%` }}
                        transition={{ duration: 0.5 }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
