const puppeteer = require("puppeteer-core");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", "--window-size=1440,900"],
  });
  const page = await browser.newPage();
  let dialogSeen = null;
  page.on("dialog", (d) => {
    dialogSeen = { type: d.type(), message: d.message().slice(0, 60) };
    console.log("DIALOG:", d.type(), JSON.stringify(d.message().slice(0, 80)));
    d.accept().catch(() => {});
  });
  page.on("response", (res) => {
    if (res.url().includes("/api/action")) {
      res.text().then((t) => console.log("ACTION RESPONSE", res.status(), t.slice(0, 200))).catch(() => {});
    }
  });
  page.on("pageerror", (e) => console.log("PAGEERROR:", String(e).slice(0, 200)));

  await page.goto("http://127.0.0.1:3000/", { waitUntil: "domcontentloaded" });
  await sleep(6000);

  // Open Local Disk (C:)
  await page.evaluate(() => {
    const card = [...document.querySelectorAll("[draggable]")].find((c) => c.textContent.includes("Local Disk (C:)"));
    const target = card ? card.querySelector("p") || card : null;
    target && target.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
  });
  await sleep(6000);
  console.log("pathname:", await page.evaluate(() => location.pathname));

  const found = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("[draggable]")];
    const card = cards.find((c) => c.textContent.includes("e2e_browser_upload"));
    if (!card) return false;
    card.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, clientX: 400, clientY: 400 }));
    return true;
  });
  console.log("card found:", found);
  await sleep(1500);
  const menuText = await page.evaluate(() => {
    const m = document.querySelector("[role='menu']");
    return m ? m.innerText : "(no menu)";
  });
  console.log("menu items:", JSON.stringify(menuText));

  await page.evaluate(() => {
    const btn = [...document.querySelectorAll("[role='menu'] button")].find((b) => b.textContent.includes("Delete"));
    if (btn) { btn.click(); return true; }
    return false;
  });
  await sleep(6000);
  console.log("dialog seen:", dialogSeen);
  await browser.close();
})().catch((e) => { console.error("FATAL", e); process.exit(1); });
