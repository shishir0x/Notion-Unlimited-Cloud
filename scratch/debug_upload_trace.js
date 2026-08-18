const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  page.on("response", async (res) => {
    const url = res.url();
    if (url.includes("/api/upload")) {
      let body = "";
      try { body = (await res.text()).slice(0, 300); } catch { /* ignore */ }
      console.log(`UPLOAD RESPONSE ${res.status()} ${url.slice(0, 80)} body=${body}`);
    }
    if (url.includes("/api/upload") || (url.includes("/api/") && !url.includes("sync/events") && !url.includes("search") && !url.includes("drive") && !url.includes("stats"))) {
      console.log(`HTTP ${res.status()} ${url.slice(0, 100)}`);
    }
  });
  page.on("requestfailed", (req) => {
    if (req.url().includes("upload")) console.log("REQUEST FAILED:", req.url(), req.failure()?.errorText);
  });

  await page.goto("http://127.0.0.1:3000/", { waitUntil: "domcontentloaded" });
  await sleep(6000);

  const uploadFile = path.join(__dirname, "e2e_trace_upload.txt");
  fs.writeFileSync(uploadFile, "trace upload\n");
  const input = await page.$("input[type='file']");
  await input.uploadFile(uploadFile);
  await sleep(15000);
  console.log("done waiting");
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
