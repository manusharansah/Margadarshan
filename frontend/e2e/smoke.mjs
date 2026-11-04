// End-to-end smoke test against a running stack (frontend + backend).
//   BASE_URL=http://localhost:3000 SHOTS=/some/dir node e2e/smoke.mjs
// Requires `playwright` (npm i -D playwright). Exits non-zero on any failed assertion.
import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const SHOTS = process.env.SHOTS;
const shot = async (page, name) =>
  SHOTS && (await page.screenshot({ path: `${SHOTS}/${name}.png` }));

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
);
const errors = [];
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
});
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on(
  "console",
  (m) =>
    m.type() === "error" &&
    !/tile|basemaps|fonts|ERR_|Failed to load resource/.test(m.text()) &&
    errors.push(`console: ${m.text()}`),
);

// 1. Load: header, status, map canvas with roads drawn
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByRole("heading", { name: "Plan a delivery" }).waitFor();
await page.waitForFunction(() => document.querySelectorAll(".leaflet-container canvas").length > 0);
await shot(page, "01-initial");

// 2. Pick destination by searching a place name
await page.getByLabel("Destination").fill("Dhul");
await page.getByRole("button", { name: /Dhulikhel/ }).click();
await page
  .getByText(/27\.\d+, 85\.\d+/)
  .first()
  .waitFor();

// 3. Submit and verify a real, feasible answer
await page
  .getByLabel("Quantity (units)")
  .or(page.locator('input[type="number"]').first())
  .first()
  .fill("400");
await page.locator('input[type="number"]').nth(1).fill("12");
await page.getByRole("button", { name: "Find best route" }).click();
await page.getByText(/Arrives in/).waitFor({ timeout: 30000 });
await page.getByText("Why this plan").waitFor();
const text = await page.locator("aside").innerText();
assert.match(text, /Distance/);
assert.match(text, /Fuel \(est\.\)/);
assert.match(text, /Rule-based|Gemma/);
await page.waitForSelector('svg[aria-label="Elevation profile of the route"]');
await shot(page, "02-result");

// 4. Hazard overlay toggles without error
await page.getByLabel("Colour roads by hazard").check();
await shot(page, "03-hazard");

// 5a. Infeasible by deadline: honest reason + suggested deadline
await page.locator('input[type="number"]').nth(1).fill("0.1");
await page.locator('input[type="number"]').first().fill("400");
await page.getByRole("button", { name: "Find best route" }).click();
await page.getByText("Can't be delivered as requested").waitFor({ timeout: 30000 });
assert.match(await page.locator("aside").innerText(), /try a deadline of at least/);
await shot(page, "04-infeasible-deadline");

// 5b. Infeasible by stock: more than the whole network holds
await page.locator('input[type="number"]').first().fill("99999");
await page.locator('input[type="number"]').nth(1).fill("24");
await page.getByRole("button", { name: "Find best route" }).click();
await page.getByText(/enough stock and vehicles/).waitFor({ timeout: 30000 });

// 5c. Clearing the destination clears the stale answer; map click pins a new one
await page.getByRole("button", { name: "Clear destination" }).click();
await page.getByText("Can't be delivered as requested").waitFor({ state: "hidden", timeout: 5000 });
await page.locator(".leaflet-container").click({ position: { x: 600, y: 300 } });
await page.getByText("Pinned location").waitFor();
await page.locator('input[type="number"]').first().fill("300");
await page.getByRole("button", { name: "Find best route" }).click();
await page
  .getByText(/Arrives in|Can't be delivered/)
  .first()
  .waitFor({ timeout: 30000 });

// 5d. Alternatives expander (when present) selects a plan
const alt = page.getByRole("button", { name: /Other feasible plans/ });
if (await alt.count()) {
  await alt.click();
  await page.locator("section[aria-label='Alternative plans'] li button").first().click();
}

// 5e. Unknown API routes / admin writes are blocked at the public edge
for (const [method, path] of [
  ["PUT", "/api/warehouses/X"],
  ["DELETE", "/api/warehouses/X"],
  ["POST", "/api/warehouses/import"],
  ["GET", "/api/nope"],
]) {
  const r = await page.request.fetch(BASE + path, { method });
  assert.equal(r.status(), 404, `${method} ${path} should be 404`);
}
assert.equal((await page.request.get(BASE + "/api/health")).status(), 200);

// 6. Other pages
await page.getByRole("link", { name: "Inventory" }).click();
await page.getByRole("heading", { name: "Inventory" }).waitFor();
await page.getByRole("cell", { name: /Sample Depot - Banepa/ }).waitFor();
await page.getByRole("cell", { name: /Total/ }).waitFor();
await shot(page, "05-inventory");
await page.getByRole("link", { name: "Data", exact: true }).click();
await page.getByRole("heading", { name: "Data & methods" }).waitFor();
await page.getByText("Data sources").waitFor();
await page.getByText("How a recommendation is made").waitFor();
await shot(page, "06-data");

// 6b. Unknown route -> friendly 404 page; deep links load directly
await page.goto(BASE + "/does-not-exist");
await page.getByText("Page not found").waitFor();
await page.goto(BASE + "/inventory", { waitUntil: "networkidle" });
await page.getByRole("heading", { name: "Inventory" }).waitFor();

// 7. Mobile layout: no horizontal overflow
const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
const mp = await m.newPage();
await mp.goto(BASE, { waitUntil: "networkidle" });
await mp.getByRole("heading", { name: "Plan a delivery" }).waitFor();
assert.equal(
  await mp.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  true,
  "horizontal overflow on mobile",
);
await shot(mp, "07-mobile");

await browser.close();
assert.deepEqual(errors, [], `browser errors:\n${errors.join("\n")}`);
console.log("E2E OK");
