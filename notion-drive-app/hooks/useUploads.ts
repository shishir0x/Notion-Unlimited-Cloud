"use client";

import { useCallback, useRef, useState } from "react";
import type { UploadTask } from "@/lib/types";

function makeId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

interface JobRef {
  file: File;
  xhr: XMLHttpRequest | null;
}

export function useUploads() {
  const [jobs, setJobs] = useState<UploadTask[]>([]);
  const refs = useRef(new Map<string, JobRef>());

  const patch = useCallback((id: string, updates: Partial<UploadTask>) => {
    setJobs((prev) => prev.map((j) => (j.id === id ? { ...j, ...updates } : j)));
  }, []);

  const uploadOne = useCallback(
    (file: File, folderId: string | null) => {
      const id = makeId();
      const ref: JobRef = { file, xhr: null };
      refs.current.set(id, ref);
      setJobs((prev) => [
        ...prev,
        { id, name: file.name, size: file.size, progress: 0, status: "pending" },
      ]);

      const xhr = new XMLHttpRequest();
      ref.xhr = xhr;
      xhr.open("POST", "/api/upload");
      xhr.setRequestHeader("Accept", "application/json");
      let lastBytes = 0;
      let lastTime = Date.now();

      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const progress = Math.min(99, Math.round((e.loaded / e.total) * 100));
        const now = Date.now();
        const dt = (now - lastTime) / 1000;
        const updates: Partial<UploadTask> = { progress };
        if (dt > 0.5) {
          const speed = (e.loaded - lastBytes) / dt;
          const remaining = e.total - e.loaded;
          lastBytes = e.loaded;
          lastTime = now;
          updates.speedBytesPerSec = speed;
          updates.etaSeconds = speed > 0 ? remaining / speed : undefined;
        }
        patch(id, updates);
      };

      xhr.onload = () => {
        let ok = false;
        let error: string | undefined;
        try {
          const data = JSON.parse(xhr.responseText) as { success?: boolean; error?: string };
          ok = Boolean(data.success);
          error = data.error;
        } catch {
          error = "Invalid server response";
        }
        if (xhr.status >= 400 || !ok) {
          patch(id, { status: "error", error: error ?? `Upload failed (${xhr.status})` });
        } else {
          patch(id, { status: "done", progress: 100 });
        }
      };
      xhr.onerror = () => {
        patch(id, { status: "error", error: "Network error" });
      };
      xhr.onabort = () => {
        setJobs((prev) => prev.filter((j) => j.id !== id));
        refs.current.delete(id);
      };

      const fd = new FormData();
      fd.append("file", file, file.name);
      if (folderId) fd.append("folder_id", folderId);
      xhr.send(fd);
    },
    [patch],
  );

  const addFiles = useCallback(
    (files: FileList | File[], folderId: string | null) => {
      for (const file of Array.from(files)) uploadOne(file, folderId);
    },
    [uploadOne],
  );

  const retry = useCallback(
    (id: string, folderId: string | null) => {
      const ref = refs.current.get(id);
      if (!ref) return;
      ref.xhr?.abort(); // cancels the onabort removal, so re-upload manually
      refs.current.delete(id);
      setJobs((prev) => prev.filter((j) => j.id !== id));
      uploadOne(ref.file, folderId);
    },
    [uploadOne],
  );

  const cancel = useCallback((id: string) => {
    refs.current.get(id)?.xhr?.abort();
  }, []);

  const remove = useCallback((id: string) => {
    refs.current.get(id)?.xhr?.abort();
    refs.current.delete(id);
    setJobs((prev) => prev.filter((j) => j.id !== id));
  }, []);

  const clearFinished = useCallback(() => {
    setJobs((prev) => prev.filter((j) => j.status === "pending" || j.status === "uploading"));
  }, []);

  const activeCount = jobs.filter((j) => j.status === "pending" || j.status === "uploading").length;
  const doneCount = jobs.filter((j) => j.status === "done").length;
  const errorCount = jobs.filter((j) => j.status === "error").length;

  return {
    jobs,
    addFiles,
    retry,
    cancel,
    remove,
    clearFinished,
    activeCount,
    doneCount,
    errorCount,
  };
}
