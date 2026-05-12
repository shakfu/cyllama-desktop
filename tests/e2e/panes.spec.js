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
  // Body slot only renders content if documents-pane.show() ran
  // without throwing.
  await expect(window.locator(".lt-tab[data-sidebar-view='documents']")).toHaveClass(/active/);
  await expect(window.locator("#docsBody")).toBeVisible();
});

test("Transcribe pane renders when whisper feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The conftest cyllama stub registers whisper, so the tab un-hides
  // itself once /info comes back.
  const tab = window.locator("#tabTranscribe");
  await expect(tab).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "transcribe");
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
  const tab = window.locator("#tabImage");
  await expect(tab).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "image");
  await expect(window.locator("#imageBody")).toContainText(/Generate/i, { timeout: 10_000 });
  // Width / Height / Steps / CFG / Seed grid lands.
  await expect(window.locator(".img-grid .dp-row")).toHaveCount(4);
});

test("Server controls render in the Preferences -> Sidecar tab", async () => {
  ctx = await launchApp();
  const { window, electron } = ctx;
  const newWindowP = electron.waitForEvent("window", { timeout: 5_000 });
  await window.click("#navPrefs");
  const prefs = await newWindowP;
  await prefs.waitForLoadState("domcontentloaded");
  await prefs.click('.prefs-nav-item[data-prefs-tab="sidecar"]');
  // Server-pane.show() mounts the Start form into #prefsServer when
  // the openai_server feature is reported by /info (conftest stub).
  await expect(prefs.locator("#prefsServer")).toContainText(/Start a server/i, { timeout: 15_000 });
  const start = prefs.locator("#sv-start");
  await expect(start).toBeVisible();
  await expect(start).toBeDisabled();
});

test("Batch pane renders prompts textarea + import + run", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const tab = window.locator("#tabBatch");
  await expect(tab).toBeVisible({ timeout: 15_000 });
  await openSidebarView(window, "batch");
  await expect(window.locator("#bt-prompts")).toBeVisible({ timeout: 10_000 });
  await expect(window.locator("#bt-run")).toBeVisible();
});

test("Parameters right-tab renders LMStudio-style sections", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Tab label is "Parameters" now; the data-tab="models" identifier
  // stays for backwards-compatibility with the persisted JS, hence
  // openRightTab(window, "models") still hits this pane.
  await expect(window.locator('.rt-tab[data-tab="models"]')).toContainText("Parameters");
  await openRightTab(window, "models");
  // System Prompt + Sampling stay open by default; the rest are
  // collapsed. Asserting on a known-open one and a known-closed
  // one keeps the test pinned to the LMStudio-style accordion IA.
  await expect(window.locator("#p-system_prompt")).toBeVisible();
  await expect(window.locator("#p-temperature")).toBeVisible();
  // Speculative section is collapsed; its draft-model picker is
  // inside <details> so it shouldn't be in the accessibility tree
  // until the user expands.
  await expect(window.locator("#p-spec_draft_model")).toBeHidden();
});

test("Models pane: full-area layout with subnav + table + detail", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Click the Models nav-rail button to switch panes.
  await window.click('.nav-btn[data-pane-jump="models"]');
  // .app picks up the pane mode; chat layout is hidden, Models pane
  // takes the work area.
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "models");
  await expect(window.locator("#modelsPane")).toBeVisible();
  // The chat children should be display:none under data-pane="models".
  await expect(window.locator(".main-col")).toBeHidden();
  await expect(window.locator(".params-panel")).toBeHidden();
  // 3-column layout: subnav + main + detail rail.
  await expect(window.locator("#modelsPaneSubnav")).toBeVisible();
  await expect(window.locator("#modelsPaneMain")).toBeVisible();
  await expect(window.locator("#modelsPaneDetail")).toBeVisible();
  // Subnav category list is populated.
  await expect(window.locator(".mp-subnav-row").first()).toContainText(/View All/i);
  // Detail rail is empty (no model selected).
  await expect(window.locator("#modelsPaneDetail")).toContainText(/Select a model/i);
});

test("Models pane: switching back to Chats restores the chat layout", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await window.click('.nav-btn[data-pane-jump="models"]');
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "models");
  await window.click('.nav-btn[data-pane-jump="chats"]');
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "chats");
  // Chat composer + send button reappear.
  await expect(window.locator("#prompt")).toBeVisible();
  await expect(window.locator("#send")).toBeVisible();
  // Models pane hidden again.
  await expect(window.locator("#modelsPane")).toBeHidden();
});

test("Agents right-tab renders the settings surface", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await openRightTab(window, "agents");
  // Agents tab is async (needs /info). Wait for its settings surface
  // to land. As of the slash-command refactor (2026-05-07) agent runs
  // are triggered from the chat composer via ``/agent <task>``; the
  // tab is settings-only -- no inline Run button.
  const host = window.locator("#agentsTabHost");
  await expect(host).toContainText(/\/agent <task>/, { timeout: 15_000 });
  await expect(host).toContainText(/Max iterations/);
  await expect(host).toContainText(/Tools/);
});

test("right-sidebar tabs are Parameters + Agents only", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The General tab moved to the standalone Preferences window
  // (Cmd+, / cog nav-rail). Right sidebar should show only the two
  // chat-side tabs now.
  const tabs = window.locator(".rt-tab");
  await expect(tabs).toHaveCount(2);
  await expect(tabs.nth(0)).toContainText("Parameters");
  await expect(tabs.nth(1)).toContainText("Agents");
  await expect(window.locator('[data-tab="general"]')).toHaveCount(0);
});

test("cog nav-rail opens the Preferences window", async () => {
  ctx = await launchApp();
  const { window, electron } = ctx;
  // Click the cog and wait for the IPC to spawn the second
  // BrowserWindow. Playwright's _electron API exposes the new
  // window via the ``window`` event.
  const newWindowP = electron.waitForEvent("window", { timeout: 5_000 });
  await window.click("#navPrefs");
  const prefs = await newWindowP;
  await prefs.waitForLoadState("domcontentloaded");
  // Sidebar carries the four category tabs; default-selected is
  // General. The presence of all four data-prefs-tab buttons is the
  // load-bearing check.
  await expect(prefs.locator(".prefs-nav-item")).toHaveCount(4);
  await expect(prefs.locator(".prefs-nav-item.active")).toContainText("General");
});

test("/constrained slash command is registered (Tab autocompletes)", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/cons");
  await prompt.press("Tab");
  // Unique-prefix autocomplete: ``/cons`` matches only ``/constrained``,
  // so Tab fills the composer with the full command + trailing space.
  await expect(prompt).toHaveValue("/constrained ");
});

test("/agent and /constrained both surface on /-prefix autocomplete", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/");
  await prompt.press("Tab");
  // Multiple matches: Tab fills the longest common prefix (empty here,
  // since ``agent`` and ``constrained`` share none) and emits a
  // candidate hint into the chat log. The log line contains both names.
  await expect(window.locator("#log")).toContainText(/\/agent/);
  await expect(window.locator("#log")).toContainText(/\/constrained/);
  await expect(window.locator("#log")).toContainText(/\/contract/);
});

test("/plan slash command is registered", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/pl");
  await prompt.press("Tab");
  await expect(prompt).toHaveValue("/plan ");
});

test("Workflows nav-button surfaces when the feature flag is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The conftest stub installs Workflow + workflow_node + agent_node,
  // so /info.features.workflow is true and the nav-rail button reveals.
  const btn = window.locator("#navWorkflows");
  await expect(btn).toBeVisible({ timeout: 15_000 });
  await btn.click();
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "workflows");
  await expect(window.locator("#workflowsPane")).toBeVisible();
  // No workflow files on disk in the test harness -> the subnav shows
  // the empty-state hint.
  await expect(window.locator("#workflowsPaneSubnav")).toContainText(/No workflow files/i);
});

test("/reflect slash + Reflection section render when feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/ref");
  await prompt.press("Tab");
  await expect(prompt).toHaveValue("/reflect ");
  await openRightTab(window, "agents");
  const host = window.locator("#agentsTabHost");
  await expect(host).toContainText(/Reflection/i, { timeout: 15_000 });
  await expect(window.locator("#ag-reflect-attempts")).toBeVisible();
  await expect(window.locator("#ag-reflect-marker")).toBeVisible();
  await expect(window.locator("#ag-reflect-critic-prompt")).toBeVisible();
});

test("Semantic memory row renders when agents.memory feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The conftest stub installs SemanticMemory so features['agents.memory']
  // is true; the row should land in the agents tab Tools section.
  await openRightTab(window, "agents");
  const host = window.locator("#agentsTabHost");
  await expect(host).toContainText(/Semantic memory/i, { timeout: 15_000 });
  await expect(window.locator("#ag-memory-enable")).toBeVisible();
  await expect(window.locator("#ag-memory-coll")).toBeVisible();
  await expect(window.locator("#ag-memory-ns")).toBeVisible();
});

test("/contract slash + Contracts section render when feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Slash registration: typing /contr completes to /contract (a longer
  // common prefix than /cons -> /constrained).
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/contr");
  await prompt.press("Tab");
  await expect(prompt).toHaveValue("/contract ");
  // Agents-tab Contracts row only renders when features['agents.contract']
  // is true; the conftest stub sets it. Wait for the section to land.
  await openRightTab(window, "agents");
  const host = window.locator("#agentsTabHost");
  await expect(host).toContainText(/Contracts/i, { timeout: 15_000 });
  await expect(window.locator("#ag-contract-preset")).toBeVisible();
  await expect(window.locator("#ag-contract-policy")).toBeVisible();
});
