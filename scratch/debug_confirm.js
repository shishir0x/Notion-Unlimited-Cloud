const puppeteer = require("puppeteer-core");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  page.on("dialog", (d) => {
    console.log("DIALOG:", d.type(), JSON.stringify(d.message().slice(0, 60)));
    d.accept();
  });
  page.on("response", (res) => {
    if (res.url().includes("/api/action")) {
      res.text().then((t) => console.log("ACTION RESP:", res.status(), t.slice(0, 150))).catch(() => {});
    }
  });
  page.on("pageerror", (e) => console.log("PAGEERROR:", String(e).slice(0, 300)));

  await page.goto("http://127.0.0.1:3000/", { waitUntil: "domcontentloaded" });
  await sleep(6000);

  const confirmResult = await page.evaluate(() => window.confirm("probe confirm"));
  console.log("window.confirm returned:", confirmResult);

  // Direct API delete of the leftover file
  const directResult = await page.evaluate(async () => {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", ids: ["3bf3d81b2f368121bb31cdf852fb241e"] }),
    });
    return { status: res.status, body: (await res.text()).slice(0, 200) };
  });
  console.log("direct delete:", directResult);
  await browser.close();
})().catch((e) => { console.error("FATAL", e); process.exit(1); });
