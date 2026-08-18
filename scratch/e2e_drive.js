/* E2E browser pass through the NotionDrive Next.js UI.
 * Drives a real browser (Edge via puppeteer-core) against http://127.0.0.1:3000
 * and exercises: root load, sidebar views, folder navigation, breadcrumbs,
 * search, grid/list toggle, preview, upload, context-menu delete, trash restore.
 */
const puppeteer = require("puppeteer-core");
const fs = require("fs");
const path = require("path");

const APP_URL = "http://127.0.0.1:3000";
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const SHOT_DIR = path.join(__dirname, "shots");
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
let failures = 0;

function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

async function waitFor(fn, timeout = 12000, label = "condition") {
  const start = Date.now();
  let lastErr;
  while (Date.now() - start < timeout) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (e) { lastErr = e; }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`Timed out waiting for ${label}${lastErr ? ` (${lastErr.message})` : ""}`);
}

async function textOf(page, sel) {
  return page.evaluate((s) => {
    const el = document.querySelector(s);
    return el ? el.textContent.trim() : null;
  }, sel);
}

// Double-click the CARD (not the grid wrapper) containing `text`. Events must
// be dispatched on an element INSIDE the card so they bubble up to the card's
// onDoubleClick handler.
async function dblclickCardContaining(page, text) {
  return page.evaluate((t) => {
    const cards = [...document.querySelectorAll("[draggable]")];
    const card = cards.find((c) => c.textContent.includes(t));
    if (!card) return false;
    const target = card.querySelector("p") || card.querySelector("span") || card;
    target.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    return true;
  }, text);
}

(async () => {
  // Clean up leftovers from previous runs (permanent-delete via BFF API).
  try {
    const res = await fetch(`${APP_URL}/api/search?q=e2e_browser_upload`);
    const data = await res.json();
    for (const item of data.items || []) {
      await fetch(`${APP_URL}/api/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete-permanent", ids: [item.id] }),
      });
    }
  } catch { /* backend may be starting */ }

  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,900"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });

  const consoleErrors = [];
  const pageErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  page.on("dialog", (d) => d.accept().catch(() => {}));

  try {
    // ── 1. Root load ────────────────────────────────────────────────────────
    // NOTE: SSE (/api/sync/events) keeps a connection open forever, so
    // networkidle0 would never settle — use domcontentloaded + explicit waits.
    await page.goto(APP_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    await waitFor(() => page.evaluate(() => document.body.innerText.includes("NotionDrive")), 15000, "app shell");

    const sidebarVisible = await page.evaluate(() =>
      ["My Drive", "Recent", "Starred", "Trash"].every((t) =>
        [...document.querySelectorAll("aside button")].some((b) => b.textContent.includes(t))));
    check("Sidebar renders with My Drive / Recent / Starred / Trash", sidebarVisible);

    await waitFor(() => page.evaluate(() =>
      [...document.querySelectorAll("button")].some((b) => b.textContent.trim() === "New")), 15000, "drive content");
    const hasNewBtn = await page.evaluate(() =>
      [...document.querySelectorAll("button")].some((b) => b.textContent.trim() === "New"));
    check("'+ New' button present", hasNewBtn);

    const liveTag = await page.evaluate(() => document.body.innerText.includes("Live"));
    check("Live (SSE) indicator connected", liveTag);

    await waitFor(() => page.evaluate(() => document.body.innerText.includes("Local Disk (C:)")), 15000, "root device folders");
    const hasRootFolders = await page.evaluate(() => document.body.innerText.includes("Local Disk (C:)"));
    check("Root drive shows device folders (Local Disk (C:))", hasRootFolders);

    // ── 2. Sidebar navigation ──────────────────────────────────────────────
    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("Recent"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/recent"), 8000, "route /recent");
    // Wait for content or the empty state (skeletons carry no "Loading" text).
    await waitFor(() => page.evaluate(() =>
      document.querySelectorAll("[draggable]").length > 0 || document.body.innerText.includes("No items")), 20000, "recent files");
    const recentCount = await page.evaluate(() => document.querySelectorAll("[draggable]").length);
    check(`Recent view shows files (${recentCount} rows/cards)`, recentCount > 0);

    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("Starred"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/starred"), 8000, "route /starred");
    await waitFor(() => page.evaluate(() =>
      document.querySelectorAll("[draggable]").length > 0 || document.body.innerText.includes("No items")), 20000, "starred content");

    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("Trash"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/trash"), 8000, "route /trash");
    await waitFor(() => page.evaluate(() =>
      document.querySelectorAll("[draggable]").length > 0 || document.body.innerText.includes("No items")), 20000, "trash content");
    check("Trash view loads without error", true);

    // ── 3. Back to My Drive ─────────────────────────────────────────────────
    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("My Drive"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/"), 8000, "route root");
    await waitFor(() => page.evaluate(() => document.body.innerText.includes("Local Disk (C:)")), 15000, "root folders after sidebar");

    // ── 4. Folder navigation + breadcrumbs ──────────────────────────────────
    // Open "Local Disk (C:)" by double-clicking its card in grid view.
    const openedFolder = await dblclickCardContaining(page, "Local Disk (C:)");
    check("Double-click opens Local Disk (C:) folder", openedFolder);
    await waitFor(() => page.evaluate(() => location.pathname.startsWith("/folder/")), 8000, "folder route");
    const crumb = await textOf(page, "nav[aria-label='Breadcrumbs']");
    check("Breadcrumbs show current folder path", crumb !== null && crumb.includes("My Drive"), crumb || "");

    // Breadcrumb back to root
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll("nav[aria-label='Breadcrumbs'] button")].find((b) => b.textContent.includes("My Drive"));
      btn && btn.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/"), 8000, "root via breadcrumb");
    await waitFor(() => page.evaluate(() => document.body.innerText.includes("Local Disk (C:)")), 15000, "root folders via breadcrumb");
    check("Breadcrumb click navigates back to My Drive", true);

    // ── 5. Search ───────────────────────────────────────────────────────────
    await page.type("input[placeholder*='Search in My Drive']", "README");
    await waitFor(() => page.evaluate(() =>
      [...document.querySelectorAll("button")].some((b) => b.textContent.includes("README"))), 10000, "search results");
    const searchHit = await page.evaluate(() => {
      const btn = [...document.querySelectorAll("button")].find((b) => b.textContent.includes("README"));
      if (btn) { btn.click(); return true; }
      return false;
    });
    check("Search finds README and opens result", searchHit);
    await waitFor(() => page.evaluate(() => !document.body.innerText.includes("Loading")), 15000, "post-search load");

    // ── 6. Grid / List toggle ───────────────────────────────────────────────
    const clickSel = (sel) => page.evaluate((s) => {
      const el = document.querySelector(s);
      if (el) { el.click(); return true; }
      return false;
    }, sel);
    const toggledList = await clickSel("button[aria-label='List view']");
    await waitFor(() => page.evaluate(() => !!document.querySelector("table")), 8000, "table view");
    check("List view renders table", toggledList && (await page.evaluate(() => !!document.querySelector("table"))));
    await clickSel("button[aria-label='Grid view']");
    await waitFor(() => page.evaluate(() => !document.querySelector("table")), 8000, "grid view restored");
    check("Grid view restored", true);

    // ── 7. Preview ──────────────────────────────────────────────────────────
    // Go to Recent and double-click the first file to open the preview modal.
    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("Recent"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/recent"), 8000, "route /recent");
    await waitFor(() => page.evaluate(() => document.querySelectorAll("[draggable]").length > 0), 15000, "recent files");
    const firstFileName = await page.evaluate(() => {
      const el = document.querySelector("[draggable]");
      return el ? (el.querySelector("p")?.textContent || el.textContent || "").slice(0, 60) : "";
    });
    await new Promise((r) => setTimeout(r, 500)); // let the listing settle
    await dblclickCardContaining(page, firstFileName.slice(0, 10));
    let previewOpened = false;
    try {
      await waitFor(() => page.evaluate(() => !!document.querySelector("[aria-label='Close preview']")), 12000, "preview modal");
      previewOpened = true;
    } catch (e) {
      await page.screenshot({ path: path.join(SHOT_DIR, "preview_fail.png") });
      const dbg = await page.evaluate(() => document.body.innerText.slice(0, 400));
      console.log("  [preview fail] body:", JSON.stringify(dbg));
    }
    const previewName = previewOpened
      ? await page.evaluate(() => document.querySelector("[aria-label='Close preview']")?.closest("div")?.textContent?.slice(0, 80) || "")
      : "";
    check("Preview modal opens for a recent file", previewOpened, previewName.split("\n")[1] || "");
    // Close preview
    await page.evaluate(() => { document.querySelector("[aria-label='Close preview']")?.click(); });
    await waitFor(() => page.evaluate(() => !document.querySelector("[aria-label='Close preview']")), 8000, "preview closed");
    check("Preview modal closes", true);

    // ── 8. Upload ───────────────────────────────────────────────────────────
    const uploadFile = path.join(__dirname, "e2e_browser_upload.txt");
    fs.writeFileSync(uploadFile, "browser upload test\n" + Date.now() + "\n");
    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("My Drive"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/"), 8000, "route root");
    await waitFor(() => page.evaluate(() => document.body.innerText.includes("Local Disk (C:)")), 15000, "root folders for upload");
    // Upload through the app's hidden file input — exercises the same onChange
    // → upload-manager pipeline the file picker uses, without chooser flakiness.
    const fileInput = await page.$("input[type='file']");
    check("Hidden file input present", !!fileInput);
    await fileInput.uploadFile(uploadFile);
    await waitFor(() => page.evaluate(() => document.body.innerText.includes("Uploads")), 10000, "upload manager");
    check("Upload manager panel appears", true);

    // ── 9. Context menu delete → trash → restore ───────────────────────────
    // The backend stores root-level uploads inside "Local Disk (C:)" — open
    // that folder and confirm the file is listed there.
    await dblclickCardContaining(page, "Local Disk (C:)");
    await waitFor(() => page.evaluate(() => location.pathname.startsWith("/folder/")), 8000, "C: folder route");
    await waitFor(() => page.evaluate(() =>
      [...document.querySelectorAll("[draggable]")].some((c) => c.textContent.includes("e2e_browser_upload"))), 30000, "uploaded file visible");
    check("Uploaded file appears in Local Disk (C:)", true);

    await page.evaluate(() => {
      const card = [...document.querySelectorAll("[draggable]")].find((c) => c.textContent.includes("e2e_browser_upload"));
      if (!card) return;
      card.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 400, clientY: 400 }));
    });
    await waitFor(() => page.evaluate(() => !!document.querySelector("[role='menu']")), 8000, "context menu");
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll("[role='menu'] button")].find((b) => b.textContent.includes("Delete"));
      btn && btn.click();
    });
    await waitFor(() => page.evaluate(() => !document.querySelector("[role='menu']")), 8000, "menu closed after delete");
    check("Context menu delete triggered", true);

    // Go to trash and confirm the item is there
    await page.evaluate(() => {
      const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("Trash"));
      b && b.click();
    });
    await waitFor(() => page.evaluate(() => location.pathname === "/trash"), 8000, "route /trash");
    await waitFor(() => page.evaluate(() => !document.body.innerText.includes("Loading")), 15000, "trash load");
    const inTrash = await page.evaluate(() => document.body.innerText.includes("e2e_browser_upload"));
    check("Deleted file appears in Trash view", inTrash);

    // Restore it
    await page.evaluate(() => {
      const card = [...document.querySelectorAll("[draggable]")].find((c) => c.textContent.includes("e2e_browser_upload"));
      if (!card) return;
      card.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 400, clientY: 400 }));
    });
    await waitFor(() => page.evaluate(() => !!document.querySelector("[role='menu']")), 8000, "trash context menu");
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll("[role='menu'] button")].find((b) => b.textContent.includes("Restore"));
      btn && btn.click();
    });
    await waitFor(() => page.evaluate(() => !document.body.innerText.includes("e2e_browser_upload") || document.body.innerText.includes("Restored")), 20000, "restored item leaves trash");
    const stillInTrash = await page.evaluate(() => document.body.innerText.includes("e2e_browser_upload"));
    check("Restore removes item from Trash", !stillInTrash);

    // ── 10. Screenshot + error report ───────────────────────────────────────
    await page.screenshot({ path: path.join(SHOT_DIR, "final.png"), fullPage: false });

    const realConsoleErrors = consoleErrors.filter((e) => !/favicon|ERR_INTERNET|net::|Failed to load resource/i.test(e));
    check("No unexpected console errors", realConsoleErrors.length === 0, realConsoleErrors.slice(0, 3).join(" | "));
    check("No uncaught page errors", pageErrors.length === 0, pageErrors.slice(0, 2).join(" | "));
  } catch (err) {
    check("E2E run completed without crash", false, err.message);
    try { await page.screenshot({ path: path.join(SHOT_DIR, "error.png") }); } catch { /* ignore */ }
  } finally {
    await browser.close();
  }

  console.log("\n==== SUMMARY ====");
  console.log(`Passed: ${results.filter((r) => r.ok).length}/${results.length}, Failed: ${failures}`);
  process.exit(failures > 0 ? 1 : 0);
})().catch((e) => { console.error("Fatal:", e); process.exit(2); });
