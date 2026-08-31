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

test("composer paperclip tooltip picks up a pinned mmproj", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The paperclip covers two independent paths: document extraction
  // (on under the conftest stub, no pinning needed) and multimodal
  // images (needs an mmproj pinned). So it starts visible, advertising
  // documents only.
  const attach = window.locator("#attach");
  await expect(attach).toBeVisible();
  await expect(attach).toHaveAttribute("title", /documents/i);
  await expect(attach).not.toHaveAttribute("title", /images/i);
  // Pin a stub path via localStorage and dispatch the same custom
  // event the Models tab fires; the chat init listener should
  // re-evaluate and add the image path to the tooltip.
  await window.evaluate(() => {
    localStorage.setItem("mmproj_path", "/stub/path/mmproj.gguf");
    window.dispatchEvent(new CustomEvent("mmproj:changed", { detail: "/stub/path/mmproj.gguf" }));
  });
  await expect(attach).toHaveAttribute("title", /images/i, { timeout: 5_000 });
  await expect(attach).toBeVisible();
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
  // Every section starts collapsed, so the tab opens as a list of
  // section heads rather than a wall of sliders. Their fields live
  // inside <details> and so aren't in the accessibility tree until
  // the user expands.
  await expect(window.locator("#p-system_prompt")).toBeHidden();
  await expect(window.locator("#p-temperature")).toBeHidden();
  await expect(window.locator("#p-spec_draft_model")).toBeHidden();
  // The heads themselves are the load-bearing content, and expanding
  // one reveals its fields.
  const sampling = window.locator(".rt-section-head", { hasText: "Sampling" });
  await expect(sampling).toBeVisible();
  await sampling.click();
  await expect(window.locator("#p-temperature")).toBeVisible();
  // Sections are independent -- expanding Sampling leaves the rest shut.
  await expect(window.locator("#p-system_prompt")).toBeHidden();
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

test("Agents pane main column renders the Common + per-type sections", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Open the Agents pane via the nav-rail. Default selection is /agent,
  // which renders the Common section (tools + max iterations) and no
  // per-type form (plain ReAct has no extra defaults).
  await window.click("#navAgents");
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "agents");
  const main = window.locator("#agentsPaneMain");
  await expect(main).toContainText(/Common/i, { timeout: 15_000 });
  await expect(main).toContainText(/Max iterations/i);
  await expect(main).toContainText(/Tools/i);
});

test("right-sidebar tabs collapse to Parameters only", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Phase F.2: Agents tab moved to a full-area pane. Right sidebar
  // shows only Parameters now. (General moved to Preferences earlier.)
  const tabs = window.locator(".rt-tab");
  await expect(tabs).toHaveCount(1);
  await expect(tabs.nth(0)).toContainText("Parameters");
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

test("/agent-constrained slash command is registered (Tab autocompletes)", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-cons");
  await prompt.press("Tab");
  // Unique-prefix: ``/agent-cons`` matches only ``agent-constrained``
  // (``agent-contract`` shares the ``agent-co`` prefix but diverges at
  // the ``n``).
  await expect(prompt).toHaveValue("/agent-constrained ");
});

test("/agent-strict alias completes to its full name", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-st");
  await prompt.press("Tab");
  // ``agent-strict`` is the only slash starting with ``agent-st``;
  // unique-prefix autocomplete completes it.
  await expect(prompt).toHaveValue("/agent-strict ");
});

test("/agent-plan opens a modal with planner/executor prompt fields", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await window.evaluate(() => {
    document.getElementById("prompt").value = "/agent-plan summarize the docs";
    document.getElementById("prompt").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
  });
  const dialog = window.locator("dialog.agent-modal");
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  await expect(dialog).toContainText(/Plan \/ execute/i);
  // Editable task field carries the slash body verbatim.
  await expect(window.locator("#am-task")).toHaveValue("summarize the docs");
  // Planner + executor prompt textareas land in the modal.
  await expect(window.locator("#am-plannerPrompt")).toBeVisible();
  await expect(window.locator("#am-executorPrompt")).toBeVisible();
  await window.keyboard.press("Escape");
});

test("Modal task field is editable and carries refined text on submit", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Open the strict modal with an initial task...
  await window.evaluate(() => {
    document.getElementById("prompt").value = "/agent-strict do thing";
    document.getElementById("prompt").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
  });
  const taskInput = window.locator("#am-task");
  await expect(taskInput).toBeVisible({ timeout: 5_000 });
  await expect(taskInput).toHaveValue("do thing");
  // ...replace the contents in-place.
  await taskInput.fill("do thing AND verify");
  await expect(taskInput).toHaveValue("do thing AND verify");
  await window.keyboard.press("Escape");
});

test("/agent-strict opens the per-call modal pre-filled with defaults", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-strict do a thing");
  // Submit via the send button if a model were loaded; the slash
  // handler opens the modal regardless of model state.
  // The keydown Enter handler gates on inFlight/sendBtn for non-nav
  // slashes, but the modal-opening path doesn't reach send() at all
  // when the button is disabled. For a deterministic test, we invoke
  // the handler manually from page context.
  await window.evaluate(async () => {
    // The render module re-exports SLASH_COMMANDS via the global for
    // tests; if not, parse + dispatch by hand.
    const p = "/agent-strict do a thing";
    const m = p.match(/^\s*\/([A-Za-z][A-Za-z0-9_-]*)\b\s*([\s\S]*)$/);
    if (!m) return;
    // Simulate the same path send() takes for an action slash.
    document.getElementById("prompt").value = p;
    document.getElementById("prompt").dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
  });
  // The modal is a <dialog class="agent-modal">; Playwright can see it
  // even though it's positioned over the rest of the page.
  await expect(window.locator("dialog.agent-modal")).toBeVisible({ timeout: 5_000 });
  await expect(window.locator("dialog.agent-modal")).toContainText(/Strict agent/i);
  // Pre-filled default format = "json".
  await expect(window.locator("#am-format")).toHaveValue("json");
  // Esc dismisses without running.
  await window.keyboard.press("Escape");
  await expect(window.locator("dialog.agent-modal")).toHaveCount(0);
});

test("/-prefix autocomplete surfaces the agent-* family", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-");
  await prompt.press("Tab");
  // Multiple matches under ``agent-``: the longest common prefix is
  // already ``agent-`` so Tab emits a candidate hint into the log
  // listing each ``/agent-...`` variant.
  await expect(window.locator("#log")).toContainText(/\/agent-constrained/);
  await expect(window.locator("#log")).toContainText(/\/agent-strict/);
  await expect(window.locator("#log")).toContainText(/\/agent-contract/);
  await expect(window.locator("#log")).toContainText(/\/agent-plan/);
  await expect(window.locator("#log")).toContainText(/\/agent-reflect/);
  await expect(window.locator("#log")).toContainText(/\/agent-workflow/);
});

test("/agent-plan slash command is registered", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-p");
  await prompt.press("Tab");
  await expect(prompt).toHaveValue("/agent-plan ");
});

test("/agent-workflow navigates to the Agents pane", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Navigation slashes bypass the Send-button gate (model not loaded
  // in the test harness), so Enter fires the handler directly.
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-workflow");
  await prompt.press("Enter");
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "agents");
  // The agent-workflow subnav row is selected on entry.
  await expect(window.locator("#agt-row-agent-workflow.active")).toBeVisible();
});

test("Agents nav-button surfaces and pane renders six agent rows", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The conftest stub installs Workflow + workflow_node + agent_node,
  // so /info.features.workflow is true and the nav-rail button reveals.
  const btn = window.locator("#navAgents");
  await expect(btn).toBeVisible({ timeout: 15_000 });
  await btn.click();
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "agents");
  await expect(window.locator("#agentsPane")).toBeVisible();
  // Subnav shows one row per agent type.
  for (const id of ["agent", "agent-strict", "agent-contract", "agent-plan", "agent-reflect", "agent-workflow"]) {
    await expect(window.locator(`#agt-row-${id}`)).toBeVisible();
  }
  // Default selection is /agent (plain ReAct).
  await expect(window.locator("#agt-row-agent.active")).toBeVisible();
});

test("/agent-reflect slash + Reflection section render when feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-r");
  await prompt.press("Tab");
  await expect(prompt).toHaveValue("/agent-reflect ");
  // Reflection defaults now live in the Agents pane main column,
  // visible when the agent-reflect subnav row is selected.
  await window.click("#navAgents");
  await window.click("#agt-row-agent-reflect");
  await expect(window.locator("#agentsPaneMain")).toContainText(/Reflection/i, { timeout: 15_000 });
  await expect(window.locator("#ag-reflect-attempts")).toBeVisible();
  await expect(window.locator("#ag-reflect-marker")).toBeVisible();
  await expect(window.locator("#ag-reflect-critic-prompt")).toBeVisible();
});

test("Semantic memory row renders in the Agents pane Common section", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // The conftest stub installs SemanticMemory so features['agents.memory']
  // is true; the row lands in the Common (tools) section on every
  // non-workflow agent type.
  await window.click("#navAgents");
  await expect(window.locator("#agentsPaneMain"))
    .toContainText(/Semantic memory/i, { timeout: 15_000 });
  await expect(window.locator("#ag-memory-enable")).toBeVisible();
  await expect(window.locator("#ag-memory-ns")).toBeVisible();
});

test("/agent-contract slash + Contract section render when feature is on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Slash registration: typing /agent-cont completes to /agent-contract
  // (a longer common prefix than /agent-cons -> /agent-constrained).
  const prompt = window.locator("#prompt");
  await prompt.click();
  await prompt.fill("/agent-cont");
  await prompt.press("Tab");
  await expect(prompt).toHaveValue("/agent-contract ");
  // Contract defaults render in the Agents pane main column when the
  // agent-contract subnav row is selected.
  await window.click("#navAgents");
  await window.click("#agt-row-agent-contract");
  await expect(window.locator("#agentsPaneMain")).toContainText(/Contract/i, { timeout: 15_000 });
  await expect(window.locator("#ag-contract-preset")).toBeVisible();
  await expect(window.locator("#ag-contract-policy")).toBeVisible();
});
