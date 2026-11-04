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
page.on("pageerror", (e) =>
  errors.push(`pageerror: ${e.message}\n${(e.stack ?? "").split("\n").slice(0, 6).join("\n")}`),
);
page.on(
  "console",
  (m) =>
    m.type() === "error" &&
    !/tile|basemaps|fonts|ERR_|Failed to load resource/.test(m.text()) &&
    errors.push(`console: ${m.text()}`),
);

// 1. Load: header, status, map canvas with roads drawn
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByRole("heading", { name: "New Shipment" }).waitFor();
await page.waitForFunction(() => document.querySelectorAll(".leaflet-container canvas").length > 0);
await shot(page, "01-initial");

// 2. Pick destination by searching a place name
await page.getByLabel("Destination").fill("Dhul");
await page
  .getByRole("button", { name: /Dhulikhel/ })
  .first()
  .click();
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
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page.getByText("Top Recommendation").waitFor({ timeout: 30000 });
await page.getByRole("button", { name: /Why this plan/ }).click();
await page.getByRole("heading", { name: "Why this plan?" }).waitFor();
const text = await page.locator("aside").innerText();
assert.match(text, /Distance/);
assert.match(text, /Fuel est\./);
assert.match(await page.getByRole("dialog").innerText(), /rule-based|Gemma/i);
await page.waitForSelector('svg[aria-label="Elevation profile of the route"]');
await shot(page, "02b-explanation");
await page.keyboard.press("Escape");
await page.getByRole("dialog").waitFor({ state: "hidden" });
await page.waitForTimeout(400);
await shot(page, "02-result");

// 4. Hazard overlay toggles without error
await page.getByLabel("Hazard exposure").uncheck();
await page.getByLabel("Hazard exposure").check();
await page.getByLabel("Road network").uncheck();
await page.getByLabel("Road network").check();
await shot(page, "03-hazard");

// 5a. Infeasible by deadline: honest reason + suggested deadline
await page.locator('input[type="number"]').nth(1).fill("0.1");
await page.locator('input[type="number"]').first().fill("3000");
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page.getByText("Cannot be fulfilled").waitFor({ timeout: 30000 });
assert.match(await page.locator("aside").innerText(), /try a deadline of at least/);
await shot(page, "04-infeasible-deadline");

// 5b. Infeasible by stock: more than the whole network holds
await page.locator('input[type="number"]').first().fill("99999");
await page.locator('input[type="number"]').nth(1).fill("24");
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page.getByText("Cannot be fulfilled").waitFor({ timeout: 30000 });

// 5c. Clearing the destination clears the stale answer; map click pins a new one
await page.getByRole("button", { name: "Clear destination" }).click();
await page.getByText("Cannot be fulfilled").waitFor({ state: "hidden", timeout: 5000 });
await page.locator(".leaflet-container").click({ position: { x: 600, y: 300 } });
await page.getByText("Pinned location").first().waitFor();
await page.locator('input[type="number"]').first().fill("300");
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page
  .getByText(/Top Recommendation|Cannot be fulfilled/)
  .first()
  .waitFor({ timeout: 30000 });

// 5d. Alternatives expander (when present) selects a plan
const alt = page.locator("[aria-label='Alternative plans'] li button");
if (await alt.count()) await alt.last().click();

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

// 5f. AI features (run with AI_E2E=1 against a backend that has Gemma configured, or the test double)
if (process.env.AI_E2E) {
  await page
    .getByRole("button", { name: "Clear destination" })
    .click()
    .catch(() => {});
  await page.getByLabel("Describe the order").fill("300 units to Dhulikhel in 12 hours");
  await page.getByRole("button", { name: /Fill the form/ }).click();
  await page.getByText(/Understood: 300 units/).waitFor({ timeout: 15000 });
  assert.equal(await page.locator('input[type="number"]').first().inputValue(), "300");
  assert.equal(await page.locator('input[type="number"]').nth(1).inputValue(), "12");
  await page.getByText("Dhulikhel", { exact: true }).first().waitFor();
  await page.getByRole("button", { name: /Find Best Route/ }).click();
  await page.getByText("Risk briefing").waitFor({ timeout: 30000 });
  // The answer is final immediately; the AI text arrives afterwards and every figure in it is filled in by code.
  await page.getByText("Top Recommendation").waitFor();
  await page.getByRole("button", { name: /Why this plan/ }).click();
  await page
    .getByText(/\[test double\] This plan arrives in \d+ ?(min|h)/)
    .waitFor({ timeout: 60000 });
  assert.doesNotMatch(await page.getByRole("dialog").innerText(), /[{}]/);
  await page.keyboard.press("Escape");
  await page.getByText(/\[test double\] Take care on .+ \(hazard \d\.\d\d\)/).waitFor();
  await shot(page, "08-ai-risk-briefing");
  // Stock-limited order: planner-verified options, some proposed by the model
  const stock = (await (await page.request.get(BASE + "/api/warehouses")).json()).warehouses.reduce(
    (t, w) => t + w.units_in_stock,
    0,
  );
  await page
    .locator('input[type="number"]')
    .first()
    .fill(String(Math.ceil(stock * 1.05)));
  await page.getByRole("button", { name: /Find Best Route/ }).click();
  await page.getByText("Options that would work").waitFor({ timeout: 30000 });
  await page.getByText("AI suggestion").first().waitFor({ timeout: 90000 }); // computed options show first, AI ones follow
  const opts = await page.locator("[aria-label='Options that would work']").innerText();
  assert.match(opts, /AI suggestion/);
  assert.match(opts, /Computed/);
  assert.match(opts, /Verified by the route planner/);
  await shot(page, "09-ai-options");
  await page
    .getByRole("button", { name: /Use this option/ })
    .first()
    .click();
  await page.getByText("Top Recommendation").waitFor({ timeout: 30000 });
  // Missing information is called out instead of guessed
  await page.getByLabel("Describe the order").fill("send some stuff soon");
  await page.getByRole("button", { name: /Fill the form/ }).click();
  await page.getByText(/Still needed/).waitFor({ timeout: 15000 });
}

// 6. Other pages
await page.getByRole("link", { name: "Inventory" }).click();
await page.getByRole("heading", { name: "Inventory" }).waitFor();
await page
  .getByRole("cell", { name: /Banepa/ })
  .first()
  .waitFor();
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
await mp.getByRole("heading", { name: "New Shipment" }).waitFor();
assert.equal(
  await mp.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  true,
  "horizontal overflow on mobile",
);
await shot(mp, "07-mobile");

await browser.close();
assert.deepEqual(errors, [], `browser errors:\n${errors.join("\n")}`);
console.log("E2E OK");
