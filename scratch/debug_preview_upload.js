const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  await sleep(8000);

  // Go to Recent
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("aside button")].find((x) => x.textContent.includes("Recent"));
    b && b.click();
  });
  await sleep(5000);
  const count = await page.evaluate(() => document.querySelectorAll("[draggable]").length);
  console.log("recent draggable count:", count);
  if (count > 0) {
    const name = await page.evaluate(() => {
      const el = document.querySelector("[draggable]");
      return el ? (el.querySelector("p")?.textContent || "").trim() : "";
    });
    console.log("first file:", JSON.stringify(name));
    await page.evaluate(() => {
      const card = document.querySelector("[draggable]");
      const target = card.querySelector("p") || card.querySelector("span") || card;
      target.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });
    await sleep(4000);
    const modalOpen = await page.evaluate(() => !!document.querySelector("[aria-label='Close preview']"));
    console.log("preview modal open:", modalOpen);
    if (!modalOpen) {
      console.log("body after dblclick:", (await page.evaluate(() => document.body.innerText)).slice(0, 300));
    } else {
      const modalText = await page.evaluate(() => document.querySelector("[aria-label='Close preview']").closest("div").innerText.slice(0, 200));
      console.log("modal text:", modalText);
    }
  }

  // Upload
  const uploadFile = path.join(__dirname, "e2e_dbg_upload.txt");
  fs.writeFileSync(uploadFile, "debug upload\n");
  const input = await page.$("input[type='file']");
  console.log("file input found:", !!input);
  if (input) {
    await input.uploadFile(uploadFile);
    await sleep(15000);
    const bodyText = await page.evaluate(() => document.body.innerText);
    const hasUploads = bodyText.includes("Uploads");
    const hasComplete = bodyText.includes("Complete");
    const hasFailed = bodyText.includes("Failed");
    console.log("uploads panel:", hasUploads, "| complete:", hasComplete, "| failed:", hasFailed);
    const panelIdx = bodyText.indexOf("Uploads");
    console.log("panel region:", bodyText.slice(panelIdx, panelIdx + 300));
  }
  console.log("console errors:", errs.slice(0, 8));
  await page.screenshot({ path: "shots/debug_preview_upload.png" });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
