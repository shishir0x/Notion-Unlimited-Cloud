const puppeteer = require("puppeteer-core");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,900"],
  });
  const page = await browser.newPage();
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERROR: " + e));
  await page.goto("http://127.0.0.1:3000/", { waitUntil: "domcontentloaded" });
  await new Promise((r) => setTimeout(r, 10000));
  const text = await page.evaluate(() => document.body.innerText.slice(0, 1500));
  console.log("=== BODY TEXT ===");
  console.log(text);
  console.log("=== draggable:", await page.evaluate(() => document.querySelectorAll("[draggable]").length));
  console.log("=== pathname:", await page.evaluate(() => location.pathname));
  console.log("=== console errors ===", errs.slice(0, 5));
  await page.screenshot({ path: "shots/debug_root.png" });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
