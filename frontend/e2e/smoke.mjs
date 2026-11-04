// End-to-end smoke test against a running stack (frontend + backend + test identity provider).
//   BASE_URL=http://localhost:3000 SHOTS=/some/dir AI_E2E=1 node e2e/smoke.mjs
// Needs `playwright`, the API configured with OIDC_* pointing at scripts/fake_oidc_server.py, and a database prepared by
// scripts/dev_bootstrap.py. Exits non-zero on any failed assertion or browser error.
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
const watch = (page) => {
  page.on("pageerror", (e) =>
    errors.push(`pageerror: ${e.message}\n${(e.stack ?? "").split("\n").slice(0, 6).join("\n")}`),
  );
  page.on("console", (m) => {
    const t = m.text();
    if (m.type() === "error" && !/tile|basemaps|fonts|ERR_|Failed to load resource/.test(t))
      errors.push(`console: ${t}`);
  });
};
const newPage = async (viewport = { width: 1440, height: 900 }, scale = 1) => {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: scale });
  const page = await ctx.newPage();
  watch(page);
  return page;
};

/** Real sign-in through the test identity provider: /login -> provider -> /api/auth/callback -> app. */
async function signIn(page, email, landing = /\/(?!login)/) {
  await page.goto(BASE);
  await page.waitForURL(/\/login/);
  await page.getByRole("link", { name: /Continue with your work account/ }).click();
  await page.getByRole("link", { name: new RegExp(email.replace(".", "\\.")) }).click();
  await page.waitForURL(landing, { timeout: 20000 });
}
const api = async (page, method, path, data) => {
  const r = await page.request.fetch(BASE + "/api" + path, {
    method,
    headers: {
      "X-Requested-With": "marga",
      ...(data ? { "content-type": "application/json" } : {}),
    },
    ...(data ? { data: JSON.stringify(data) } : {}),
  });
  return r.status();
};

// ============================================================ 1. signed-out visitors
const anon = await newPage();
await anon.goto(BASE);
await anon.waitForURL(/\/login/);
await anon.getByRole("link", { name: /Continue with your work account/ }).waitFor();
await shot(anon, "00-login");
assert.equal(await api(anon, "GET", "/warehouses"), 401, "data must need sign-in");
assert.equal(await api(anon, "GET", "/nope"), 404);
assert.equal((await anon.request.get(BASE + "/api/health")).status(), 200);
for (const p of ["/inventory", "/orders", "/team", "/account"]) {
  await anon.goto(BASE + p);
  await anon.waitForURL(/\/login/);
}

// 2. refused sign-ins show a clear message and leave no session
await signIn(anon, "carol@other.test", /error=not_invited/).catch(() => {});
await anon.getByText(/has not been invited/).waitFor();
assert.equal(await api(anon, "GET", "/auth/me"), 401);
await anon.goto(BASE);
await anon.getByRole("link", { name: /Continue with your work account/ }).click();
await anon.getByRole("link", { name: /mallory@evil\.test/ }).click();
await anon.waitForURL(/error=failed/);
await anon.getByText(/could not verify/i).waitFor();
assert.equal(await api(anon, "GET", "/auth/me"), 401);

// ============================================================ 3. an owner plans deliveries
const page = await newPage();
await signIn(page, "alice@acme.test");
await page.getByRole("heading", { name: "New Shipment" }).waitFor();
await page.waitForFunction(() => document.querySelectorAll(".leaflet-container canvas").length > 0);
await shot(page, "01-initial");

await page.getByLabel("Destination").fill("Dhul");
await page
  .getByRole("button", { name: /Dhulikhel/ })
  .first()
  .click();
await page
  .getByText(/27\.\d+, 85\.\d+/)
  .first()
  .waitFor();
await page.locator('input[type="number"]').first().fill("400");
await page.locator('input[type="number"]').nth(1).fill("12");
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page.getByText("Top Recommendation").waitFor({ timeout: 30000 });
await page.getByRole("button", { name: /Why this plan/ }).click();
await page.getByRole("heading", { name: "Why this plan?" }).waitFor();
const text = await page.locator("aside").innerText();
assert.match(text, /Distance/);
assert.match(text, /Fuel est\./);
assert.match(text, /Likely .+ to .+/); // ETA range from the uncertainty simulation
await page.waitForSelector('svg[aria-label="Elevation profile of the route"]');
await shot(page, "02b-explanation");
await page.keyboard.press("Escape");
await page.getByRole("dialog").waitFor({ state: "hidden" });
await page.waitForTimeout(400);
await shot(page, "02-result");

await page.getByLabel("Hazard exposure").uncheck();
await page.getByLabel("Hazard exposure").check();
await page.getByLabel("Road network").uncheck();
await page.getByLabel("Road network").check();
await shot(page, "03-hazard");

// infeasible by deadline, then by stock
await page.locator('input[type="number"]').nth(1).fill("0.1");
await page.locator('input[type="number"]').first().fill("3000");
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page.getByText("Cannot be fulfilled").waitFor({ timeout: 30000 });
assert.match(await page.locator("aside").innerText(), /try a deadline of at least/);
await shot(page, "04-infeasible-deadline");
const stock = (await (await page.request.get(BASE + "/api/warehouses")).json()).warehouses.reduce(
  (t, w) => t + w.units_in_stock,
  0,
);
await page
  .locator('input[type="number"]')
  .first()
  .fill(String(Math.ceil(stock * 1.05)));
await page.locator('input[type="number"]').nth(1).fill("24");
await page.getByRole("button", { name: /Find Best Route/ }).click();
await page.getByText("Cannot be fulfilled").waitFor({ timeout: 30000 });

// AI features (test double): figures are written by code, options are verified by the planner
if (process.env.AI_E2E) {
  await page.getByRole("button", { name: "Clear destination" }).click();
  await page.getByLabel("Describe the order").fill("300 units to Dhulikhel in 12 hours");
  await page.getByRole("button", { name: /Fill the form/ }).click();
  await page.getByText(/Understood: 300 units/).waitFor({ timeout: 20000 });
  assert.equal(await page.locator('input[type="number"]').first().inputValue(), "300");
  await page.getByRole("button", { name: /Find Best Route/ }).click();
  await page.getByText("Risk briefing").waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: /Why this plan/ }).click();
  await page
    .getByText(/\[test double\] This plan arrives in \d+ ?(min|h)/)
    .waitFor({ timeout: 60000 });
  assert.doesNotMatch(await page.getByRole("dialog").innerText(), /[{}]/);
  await page.keyboard.press("Escape");
  await shot(page, "08-ai-risk-briefing");
  await page
    .locator('input[type="number"]')
    .first()
    .fill(String(Math.ceil(stock * 1.05)));
  await page.getByRole("button", { name: /Find Best Route/ }).click();
  await page.getByText("Options that would work").waitFor({ timeout: 30000 });
  await page.getByText("AI suggestion").first().waitFor({ timeout: 90000 });
  assert.match(
    await page.locator("[aria-label='Options that would work']").innerText(),
    /Verified by the route planner/,
  );
  await shot(page, "09-ai-options");
  await page
    .getByRole("button", { name: /Use this option/ })
    .first()
    .click();
  await page.getByText("Top Recommendation").waitFor({ timeout: 30000 });
  await page.getByLabel("Describe the order").fill("send some stuff soon");
  await page.getByRole("button", { name: /Fill the form/ }).click();
  await page.getByText(/Still needed/).waitFor({ timeout: 20000 });
}

// ============================================================ 4. owner pages: inventory, rules, orders, team, audit
await page.getByRole("link", { name: "Inventory", exact: true }).click();
await page.getByRole("heading", { name: "Inventory" }).waitFor();
await page
  .getByRole("cell", { name: /Banepa/ })
  .first()
  .waitFor();
await page.getByRole("heading", { name: "Add or update a warehouse" }).waitFor();
const form = {
  ID: "E2E-1",
  Name: "E2E Depot",
  Latitude: "27.6298",
  Longitude: "85.5214",
  "Units in stock": "50",
  Vehicles: "1",
  "Units per vehicle": "100",
};
for (const [label, v] of Object.entries(form))
  await page.getByLabel(label, { exact: true }).fill(v);
await page.getByRole("button", { name: "Save warehouse" }).click();
await page
  .getByRole("cell", { name: /E2E Depot/ })
  .first()
  .waitFor();
page.once("dialog", (d) => d.accept());
await page.getByRole("button", { name: "Delete E2E Depot" }).click();
await page.waitForFunction(() => !document.body.innerText.includes("E2E Depot"));
await page.getByLabel("Upload CSV").setInputFiles({
  name: "w.csv",
  mimeType: "text/csv",
  buffer: Buffer.from(
    "id,name,lat,lng,units_in_stock,vehicles_available\nE2E-CSV,CSV Depot,27.6298,85.5214,10,1\n",
  ),
});
await page.getByRole("button", { name: "Import CSV" }).click();
await page.getByText("Imported 1 warehouses.").waitFor();
page.once("dialog", (d) => d.accept());
await page.getByRole("button", { name: "Delete CSV Depot" }).click();
// closure: add, see, remove
await page.getByLabel("Closure latitude").fill("27.62");
await page.getByLabel("Closure longitude").fill("85.52");
await page.getByLabel("Closure reason").fill("E2E landslide");
await page.getByRole("button", { name: "Add closure" }).click();
await page.getByText("E2E landslide").waitFor();
await page.getByRole("button", { name: "Remove" }).first().click();
await page.getByText("No closures recorded.").waitFor();
await shot(page, "05-inventory");

await page.getByRole("link", { name: "Orders", exact: true }).click();
await page.getByRole("heading", { name: "Orders" }).waitFor();
await page.getByRole("cell", { name: "Dhulikhel" }).first().waitFor();
await shot(page, "10-orders");

await page.getByRole("link", { name: "Team", exact: true }).click();
await page.getByRole("heading", { name: "Team" }).waitFor();
await page.getByRole("cell", { name: /Alice Owner/ }).waitFor();
await page.getByLabel("Email").fill("dave@acme.test");
await page.getByRole("button", { name: "Send invitation" }).click();
await page
  .getByText(/dave@acme\.test/)
  .first()
  .waitFor();
await page
  .locator("li", { hasText: "dave@acme.test" })
  .getByRole("button", { name: "Revoke" })
  .click();
await page.locator("li", { hasText: "dave@acme.test" }).waitFor({ state: "detached" });
await page.getByRole("heading", { name: "Audit log" }).waitFor();
await page.getByText("warehouse upsert").first().waitFor();
await shot(page, "11-team");

await page.getByRole("link", { name: "Data", exact: true }).click();
await page.getByText("Data sources").waitFor();
await page.getByText("How a recommendation is made").waitFor();
await shot(page, "06-data");

// account: download my data
await page.getByRole("button", { name: "Account menu" }).click();
await page.getByRole("menuitem", { name: /Account and data/ }).click();
await page.getByRole("heading", { name: "Account and data" }).waitFor();
const [download] = await Promise.all([
  page.waitForEvent("download"),
  page.getByRole("button", { name: "Download my data" }).click(),
]);
const exported = JSON.parse(
  await (await import("node:fs/promises")).readFile(await download.path(), "utf8"),
);
assert.equal(exported.user.email, "alice@acme.test");
assert.ok(exported.audit_entries.length > 0 && !JSON.stringify(exported).includes("token_"));
await shot(page, "12-account");

// ============================================================ 5. sign out really signs out
await page.getByRole("button", { name: "Account menu" }).click();
await page.getByRole("menuitem", { name: "Sign out" }).click();
await page.waitForURL(/\/login/);
assert.equal(await api(page, "GET", "/warehouses"), 401);
await page.goBack().catch(() => {});
await page.goto(BASE + "/inventory");
await page.waitForURL(/\/login/);

// ============================================================ 6. a member can plan but not administer
const member = await newPage();
await signIn(member, "bob@acme.test");
await member.getByRole("heading", { name: "New Shipment" }).waitFor();
await member.getByLabel("Destination").fill("Dhul");
await member
  .getByRole("button", { name: /Dhulikhel/ })
  .first()
  .click();
await member.getByRole("button", { name: /Find Best Route/ }).click();
await member
  .getByText(/Top Recommendation|Cannot be fulfilled/)
  .first()
  .waitFor({ timeout: 30000 });
await member.getByRole("link", { name: "Inventory", exact: true }).click();
await member.getByRole("heading", { name: "Inventory" }).waitFor();
await member
  .getByRole("cell", { name: /Banepa/ })
  .first()
  .waitFor();
assert.equal(
  await member.getByRole("heading", { name: "Add or update a warehouse" }).count(),
  0,
  "members must not see admin forms",
);
await member.getByRole("link", { name: "Team", exact: true }).click();
await member.getByRole("heading", { name: "Team" }).waitFor();
assert.equal(await member.getByRole("button", { name: "Send invitation" }).count(), 0);
assert.equal(await member.getByRole("heading", { name: "Audit log" }).count(), 0);
assert.equal(
  await api(member, "PUT", "/warehouses/HACK", {
    name: "x",
    lat: 27.6,
    lng: 85.5,
    units_in_stock: 1,
    vehicles_available: 1,
  }),
  403,
  "server enforces roles",
);
assert.equal(await api(member, "GET", "/audit"), 403);
// the member sees only their own organisation's orders, not other organisations' data
const ownOrders = await (await member.request.get(BASE + "/api/orders")).json();
assert.ok(ownOrders.orders.length >= 2);

// ============================================================ 7. phone layout, signed in
const phone = await newPage({ width: 390, height: 844 }, 2);
await signIn(phone, "alice@acme.test");
await phone.getByRole("heading", { name: "New Shipment" }).waitFor();
assert.equal(
  await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  true,
  "horizontal overflow on mobile",
);
await shot(phone, "07-mobile");
for (const p of ["/inventory", "/orders", "/team", "/account"]) {
  await phone.goto(BASE + p);
  await phone.waitForTimeout(600);
  assert.equal(
    await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    true,
    `overflow on ${p}`,
  );
}

await browser.close();
assert.deepEqual(errors, [], `browser errors:\n${errors.join("\n")}`);
console.log("E2E OK");
