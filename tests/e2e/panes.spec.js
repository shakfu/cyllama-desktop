// Per-pane smoke tests. Each test boots a fresh Electron + stub-sidecar
// instance, opens the surface under test, and asserts that the pane
// renders its expected primary content. The intent is to catch
// "renderer dispatched the wrong shape to the sidecar" or "feature
// flag wiring regressed and the pane stayed hidden" -- *not* to
// exercise the full feature path (the pytest suite covers that).

const { test, expect } = require("@playwright/test");
const { launchApp, openSidebarView, openRightTab } = require("./_harness.js");

let ctx;
test.afterEach(async () => {
  if (ctx) {
    await ctx.cleanup();
    ctx = null;
  }
});

test("composer paperclip surfaces when an mmproj is pinned", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Hidden until an mmproj path is pinned, even though
  // /info.features.multimodal is true under the conftest stub.
  await expect(window.locator("#attach")).toBeHidden();
  // Pin a stub path via localStorage and dispatch the same custom
  // event the Models tab fires; the chat init listener should
  // re-evaluate and reveal the paperclip.
  await window.evaluate(() => {
    localStorage.setItem("mmproj_path", "/stub/path/mmproj.gguf");
    window.dispatchEvent(new CustomEvent("mmproj:changed", { detail: "/stub/path/mmproj.gguf" }));
  });
  await expect(window.locator("#attach")).toBeVisible({ timeout: 5_000 });
});

test("Chat pane renders by default", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The chats sidebar-view is active on launch; the empty state in
  // the conversation column is the most stable selector that proves
  // the renderer's first paint completed without throwing.
  await expect(window.locator(".sidebar-view[data-view='chats'].active")).toBeVisible();
  await expect(window.locator(".empty-title").first()).toContainText(/Local inference/i);
  // Composer + send button wired up.
  await expect(window.locator("#prompt")).toBeVisible();
  await expect(window.locator("#send")).toBeVisible();
});

test("Documents pane mounts with collections list", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await openSidebarView(window, "documents");
  // Header is the load-bearing assertion -- it only renders if
  // documents-pane.show() ran without throwing.
  await expect(window.locator(".sidebar-view[data-view='documents'] h2")).toContainText("Documents");
  // The empty-state copy from the Documents pane should land within
  // the body slot.
  await expect(window.locator("#docsBody")).toBeVisible();
});

test("Transcribe pane renders when whisper feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The conftest cyllama stub registers whisper, so the nav button
  // un-hides itself once /info comes back.
  const nav = window.locator("#navTranscribe");
  await expect(nav).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "transcribe");
  await expect(window.locator(".sidebar-view[data-view='transcribe'] h2")).toContainText("Transcribe");
  // Build is async (model list fetch). Wait for the Input section
  // to land.
  await expect(window.locator("#transcribeBody")).toContainText(/Input/i, { timeout: 10_000 });
  // Transcribe button surfaces in the actions row, disabled until
  // a model + audio file are picked.
  await expect(window.locator("#tx-run")).toBeVisible();
});

test("Image pane renders when SD feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const nav = window.locator("#navImage");
  await expect(nav).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "image");
  await expect(window.locator(".sidebar-view[data-view='image'] h2")).toContainText("Image");
  await expect(window.locator("#imageBody")).toContainText(/Generate/i, { timeout: 10_000 });
  // Width / Height / Steps / CFG / Seed grid lands.
  await expect(window.locator(".img-grid .dp-row")).toHaveCount(4);
});

test("Server pane shows the Start form when idle", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const nav = window.locator("#navServer");
  await expect(nav).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "server");
  await expect(window.locator(".sidebar-view[data-view='server'] h2")).toContainText("Server");
  await expect(window.locator("#serverBody")).toContainText(/Start a server/i, { timeout: 10_000 });
  // Start button disabled until a model is picked.
  const start = window.locator("#sv-start");
  await expect(start).toBeVisible();
  await expect(start).toBeDisabled();
});

test("Batch pane renders prompts textarea + import + run", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const nav = window.locator("#navBatch");
  await expect(nav).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "batch");
  await expect(window.locator(".sidebar-view[data-view='batch'] h2")).toContainText("Batch");
  await expect(window.locator("#bt-prompts")).toBeVisible({ timeout: 10_000 });
  await expect(window.locator("#bt-run")).toBeVisible();
});

test("Models right-tab loads the cached models slot", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await openRightTab(window, "models");
  // The static System Prompt + Sampling sections live next to the
  // dynamic models slot; the slot itself should populate from the
  // sidecar's empty cached list.
  await expect(window.locator("#modelsTabHost")).toBeVisible();
  // System-prompt textarea is part of this tab and one of the
  // chat-side bindings depends on it; assert it surfaced.
  await expect(window.locator("#p-system_prompt")).toBeVisible();
});

test("Agents right-tab renders the Run section", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await openRightTab(window, "agents");
  // Agents tab is async (needs /info). Wait for its Run header to
  // land rather than asserting on the placeholder.
  await expect(window.locator("#agentsTabHost")).toContainText(/Run/i, { timeout: 15_000 });
  await expect(window.locator("#ag-run")).toBeVisible();
});

test("General right-tab shows About + Devices", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await openRightTab(window, "general");
  await expect(window.locator("#generalTabHost")).toContainText(/About/i, { timeout: 10_000 });
  await expect(window.locator("#generalTabHost")).toContainText(/Devices/i);
  // cyllama version shows "0.0.0-test" because conftest's stub sets
  // ``__version__`` to that. Doubles as a check that /info is being
  // consumed end-to-end through the bridge.
  await expect(window.locator("[data-bind='version']")).toContainText("0.0.0-test");
});
