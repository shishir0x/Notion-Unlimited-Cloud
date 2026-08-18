"""
launcher.py — Unified Application Orchestrator for Notion Unlimited Cloud.

Starts and manages:
  1. Next.js Web Application (http://localhost:3000)
  2. Direct Notion Database Connection
  3. Real-time readiness polling and browser launching
  4. Interactive Terminal Sync CLI
  5. Clean graceful shutdown on Ctrl+C or exit
"""

import os
import sys
import time
import signal
import shutil
import urllib.request
import urllib.error
import subprocess
import webbrowser
from pathlib import Path

# Ensure UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent
NEXT_APP_DIR = PROJECT_ROOT / "notion-drive-app"
FRONTEND_PORT = int(os.environ.get("PORT", "3000"))

_subprocesses = []


def cleanup(sig=None, frame=None):
    """Terminate all spawned child processes cleanly."""
    print("\n🛑 Shutting down Notion Unlimited Cloud...")
    for proc in _subprocesses:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    print("👋 Application stopped.")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)


def check_prerequisites():
    """Verify Node, npm, Python, and .env configuration."""
    print("🔍 Checking prerequisites...")

    # Check Node.js
    if not shutil.which("node"):
        print("\n❌ Error: Node.js is not installed or not in PATH.")
        print("Please install Node.js 18+ from https://nodejs.org\n")
        sys.exit(1)

    # Check npm
    if not shutil.which("npm"):
        print("\n❌ Error: npm is not installed or not in PATH.\n")
        sys.exit(1)

    # Check .env
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print("\n⚠️  No .env file found. Launching setup wizard...")
        import setup
        ok = setup.run_setup()
        if not ok:
            print("❌ Setup incomplete. Exiting.")
            sys.exit(1)

    # Sync .env to Next.js app .env.local
    next_env = NEXT_APP_DIR / ".env.local"
    if env_file.exists() and not next_env.exists():
        try:
            shutil.copyfile(env_file, next_env)
        except Exception:
            pass

    # Check next node_modules
    if not (NEXT_APP_DIR / "node_modules").exists():
        print("📦 Installing Next.js dependencies (first run)...")
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        subprocess.run([npm_cmd, "install"], cwd=str(NEXT_APP_DIR), check=True)

    print("✅ Prerequisites verified.\n")


def wait_for_health(url: str, name: str, timeout_sec: int = 30) -> bool:
    """Poll a health endpoint until 200 OK or timeout."""
    start = time.time()
    sys.stdout.write(f"⏳ Starting {name} ({url})...")
    sys.stdout.flush()

    while time.time() - start < timeout_sec:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HealthCheck/1.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    sys.stdout.write(" ✅ Ready!\n")
                    sys.stdout.flush()
                    return True
        except Exception:
            pass
        time.sleep(0.5)
        sys.stdout.write(".")
        sys.stdout.flush()

    sys.stdout.write(" ❌ Timeout!\n")
    sys.stdout.flush()
    return False


def start_nextjs():
    """Start Next.js web application on port 3000."""
    print(f"🚀 Starting Next.js Web App on http://localhost:{FRONTEND_PORT}...")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=str(NEXT_APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _subprocesses.append(proc)
    return proc


def main():
    print("=" * 64)
    print("  ☁️  NOTION UNLIMITED CLOUD — APPLICATION LAUNCHER")
    print("=" * 64)
    print()

    check_prerequisites()

    # 1. Start Next.js on port 3000
    start_nextjs()
    frontend_ok = wait_for_health(f"http://127.0.0.1:{FRONTEND_PORT}", "Next.js Web App", timeout_sec=30)
    if not frontend_ok:
        print("⚠️  Warning: Next.js did not respond to readiness probe in time.")

    # 2. Open browser
    app_url = f"http://localhost:{FRONTEND_PORT}"
    print(f"\n🌐 Opening browser at: {app_url}\n")
    webbrowser.open(app_url)

    # 3. Launch interactive terminal CLI in the foreground
    print("=" * 64)
    print("  🎉 Next.js Drive running on port 3000!")
    print("  Terminal Sync CLI is active below. Press Ctrl+C to stop.")
    print("=" * 64)
    print()

    try:
        if sys.stdin and sys.stdin.isatty():
            import notion_sync
            notion_sync.interactive_menu()
        else:
            print("  Serving Next.js on port 3000. Press Ctrl+C to exit.")
            while True:
                time.sleep(1)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    main()
