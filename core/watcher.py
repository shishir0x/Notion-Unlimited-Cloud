"""
core/watcher.py — Filesystem watcher with debounce for live synchronization.

Monitors local directories for changes and queues them for sync.
Uses platform-specific watchers where available, falls back to polling.
"""

import os
import time
import threading
import logging
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger("notion_cloud.watcher")

# Event types
EVENT_CREATED = "created"
EVENT_MODIFIED = "modified"
EVENT_DELETED = "deleted"
EVENT_MOVED = "moved"
EVENT_RENAMED = "renamed"


@dataclass
class FileEvent:
    """Represents a filesystem event."""
    event_type: str
    path: str
    old_path: Optional[str] = None
    is_directory: bool = False
    timestamp: float = field(default_factory=time.time)
    size: int = 0
    mtime: float = 0


class Debouncer:
    """Debounces rapid filesystem events for the same path."""
    
    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self._timers: Dict[str, threading.Timer] = {}
        self._events: Dict[str, FileEvent] = {}
        self._lock = threading.Lock()
    
    def add(self, event: FileEvent, callback: Callable[[FileEvent], None]):
        """Add an event, debouncing rapid duplicates."""
        with self._lock:
            # Cancel existing timer for this path
            if event.path in self._timers:
                self._timers[event.path].cancel()
            
            # Keep the most recent event
            self._events[event.path] = event
            
            # Set new timer
            timer = threading.Timer(self.delay, self._fire, args=[event.path, callback])
            timer.daemon = True
            self._timers[event.path] = timer
            timer.start()
    
    def _fire(self, path: str, callback: Callable[[FileEvent], None]):
        """Fire the debounced event."""
        with self._lock:
            event = self._events.pop(path, None)
            timer = self._timers.pop(path, None)
        
        if event and callback:
            callback(event)
    
    def flush(self, callback: Callable[[FileEvent], None]):
        """Immediately fire all pending events."""
        with self._lock:
            events = list(self._events.values())
            self._events.clear()
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        
        for event in events:
            callback(event)
    
    def cancel(self):
        """Cancel all pending events."""
        with self._lock:
            self._events.clear()
            for timer in self._timers.values():
                try:
                    timer.cancel()
                except Exception:
                    pass
            self._timers.clear()


class FileSystemWatcher:
    """
    Cross-platform filesystem watcher with debouncing.
    
    Falls back to periodic polling on platforms without native watcher support.
    """
    
    def __init__(self, debounce_delay: float = 2.0, poll_interval: float = 5.0):
        self.debounce_delay = debounce_delay
        self.poll_interval = poll_interval
        self.debouncer = Debouncer(delay=debounce_delay)
        self._watched_paths: Dict[str, Dict[str, Any]] = {}
        self._callbacks: List[Callable[[FileEvent], None]] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._watcher = None  # Platform-specific watcher instance
    
    def add_callback(self, callback: Callable[[FileEvent], None]):
        """Add a callback for file events."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[FileEvent], None]):
        """Remove a callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)
    
    def watch(self, path: str, recursive: bool = True):
        """Start watching a path for changes."""
        path = str(Path(path).resolve())
        with self._lock:
            if path not in self._watched_paths:
                self._watched_paths[path] = {
                    'recursive': recursive,
                    'mtime_cache': {},
                    'exists_cache': set()
                }
                logger.info(f"Watching: {path} (recursive={recursive})")
    
    def unwatch(self, path: str):
        """Stop watching a path."""
        path = str(Path(path).resolve())
        with self._lock:
            if path in self._watched_paths:
                del self._watched_paths[path]
                logger.info(f"Unwatched: {path}")
    
    def start(self):
        """Start the watcher thread."""
        if self._running:
            return
        
        self._running = True
        
        # Try to use platform-specific watcher
        if sys_has_watchdog():
            self._start_watchdog()
        else:
            self._start_polling()
        
        logger.info("FileSystemWatcher started")
    
    def stop(self):
        """Stop the watcher thread."""
        self._running = False
        
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None
        
        self.debouncer.cancel()
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        
        logger.info("FileSystemWatcher stopped")
    
    def _start_watchdog(self):
        """Start watchdog-based watcher (more efficient)."""
        def _run():
            try:
                from watchdog.observers import Observer
                from watchdog.events import FileSystemEventHandler, FileSystemEvent
                
                class Handler(FileSystemEventHandler):
                    def __init__(self, watcher):
                        self.watcher = watcher
                    
                    def on_created(self, event):
                        if not event.is_directory:
                            self.watcher._handle_event(FileEvent(
                                event_type=EVENT_CREATED,
                                path=event.src_path,
                                is_directory=event.is_directory
                            ))
                    
                    def on_modified(self, event):
                        if not event.is_directory:
                            self.watcher._handle_event(FileEvent(
                                event_type=EVENT_MODIFIED,
                                path=event.src_path,
                                is_directory=event.is_directory
                            ))
                    
                    def on_deleted(self, event):
                        self.watcher._handle_event(FileEvent(
                            event_type=EVENT_DELETED,
                            path=event.src_path,
                            is_directory=event.is_directory
                        ))
                    
                    def on_moved(self, event):
                        self.watcher._handle_event(FileEvent(
                            event_type=EVENT_MOVED,
                            path=event.dest_path,
                            old_path=event.src_path,
                            is_directory=event.is_directory
                        ))
                
                observer = Observer()
                handler = Handler(self)
                
                with self._lock:
                    for path in self._watched_paths:
                        observer.schedule(handler, path, recursive=True)
                
                self._watcher = observer
                observer.start()
                
                while self._running:
                    time.sleep(0.5)
                
                observer.stop()
                observer.join()
            except ImportError:
                logger.warning("watchdog not available, falling back to polling")
                self._start_polling()
            except Exception as e:
                logger.error(f"Watchdog error: {e}, falling back to polling")
                self._start_polling()
        
        self._thread = threading.Thread(target=_run, daemon=True, name="FSWatcher")
        self._thread.start()
    
    def _start_polling(self):
        """Start polling-based watcher (fallback)."""
        def _run():
            while self._running:
                try:
                    self._poll()
                except Exception as e:
                    logger.error(f"Poll error: {e}")
                time.sleep(self.poll_interval)
        
        self._poll_thread = threading.Thread(target=_run, daemon=True, name="FSPoll")
        self._poll_thread.start()
    
    def _poll(self):
        """Poll watched paths for changes."""
        with self._lock:
            paths = dict(self._watched_paths)
        
        for path, config in paths.items():
            if not Path(path).exists():
                continue
            
            try:
                self._scan_path(path, config)
            except Exception as e:
                logger.error(f"Error scanning {path}: {e}")
    
    def _scan_path(self, root: str, config: Dict):
        """Scan a path and detect changes."""
        current_files: Dict[str, float] = {}
        current_dirs: set = set()
        
        try:
            for dirpath, dirs, files in os.walk(root):
                # Track directories
                for d in dirs:
                    dir_full = str(Path(dirpath) / d)
                    current_dirs.add(dir_full)
                    try:
                        mtime = Path(dir_full).stat().st_mtime
                        current_files[dir_full] = mtime
                    except OSError:
                        pass
                
                # Track files
                for f in files:
                    file_full = str(Path(dirpath) / f)
                    try:
                        st = Path(file_full).stat()
                        current_files[file_full] = st.st_mtime
                    except OSError:
                        pass
                
                if not config['recursive']:
                    break
        except OSError as e:
            logger.error(f"Cannot scan {root}: {e}")
            return
        
        # Compare with cache
        prev = config['mtime_cache']
        prev_exists = config['exists_cache']
        current_exists = set(current_files.keys())
        
        # Detect new files
        for path in current_exists - prev_exists:
            self._handle_event(FileEvent(
                event_type=EVENT_CREATED,
                path=path,
                is_directory=path in current_dirs
            ))
        
        # Detect deleted files
        for path in prev_exists - current_exists:
            self._handle_event(FileEvent(
                event_type=EVENT_DELETED,
                path=path,
                is_directory=path in current_dirs  # approximate
            ))
        
        # Detect modified files
        for path in current_exists & prev_exists:
            if abs(current_files[path] - prev.get(path, 0)) > 1.0:
                self._handle_event(FileEvent(
                    event_type=EVENT_MODIFIED,
                    path=path,
                    is_directory=path in current_dirs,
                    mtime=current_files[path]
                ))
        
        # Update cache
        config['mtime_cache'] = current_files
        config['exists_cache'] = current_exists
    
    def _handle_event(self, event: FileEvent):
        """Handle a filesystem event with debouncing."""
        # Get file size if it exists
        if event.event_type != EVENT_DELETED:
            try:
                p = Path(event.path)
                if p.exists() and not event.is_directory:
                    event.size = p.stat().st_size
                    event.mtime = p.stat().st_mtime
            except OSError:
                pass
        
        self.debouncer.add(event, self._dispatch_event)
    
    def _dispatch_event(self, event: FileEvent):
        """Dispatch event to all callbacks."""
        with self._lock:
            callbacks = list(self._callbacks)
        
        for callback in callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def flush(self):
        """Flush all pending events."""
        self.debouncer.flush(self._dispatch_event)


def sys_has_watchdog() -> bool:
    """Check if watchdog library is available."""
    try:
        import watchdog
        return True
    except ImportError:
        return False


# Global watcher instance
_watcher: Optional[FileSystemWatcher] = None


def get_watcher() -> FileSystemWatcher:
    """Get the global watcher instance."""
    global _watcher
    if _watcher is None:
        _watcher = FileSystemWatcher()
    return _watcher


def start_watcher():
    """Start the global watcher."""
    get_watcher().start()


def stop_watcher():
    """Stop the global watcher."""
    global _watcher
    if _watcher:
        _watcher.stop()
        _watcher = None