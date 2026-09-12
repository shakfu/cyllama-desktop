// Per-pane smoke tests. Each test boots a fresh Electron + stub-sidecar
// instance, opens the surface under test, and asserts that the pane
// renders its expected primary content. The intent is to catch
// "renderer dispatched the wrong shape to the sidecar" or "feature
// flag wiring regressed and the pane stayed hidden" -- *not* to
// exercise the full feature path (the pytest suite covers that).

const fs = require("fs");
const path = require("path");
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

test("sidebar tab strip fits every tab when all features are on", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await expect(window.locator("#tabBatch")).toBeVisible();
  const m = await window.evaluate(() => {
    const strip = document.querySelector(".lt-tabs").getBoundingClientRect();
    return [...document.querySelectorAll(".lt-tab:not([hidden])")].map((t) => ({
      label: t.textContent,
      right: t.getBoundingClientRect().right,
      stripRight: strip.right,
      clipped: t.scrollWidth > t.clientWidth,
    }));
  });
  expect(m.map((t) => t.label)).toEqual(["Chats", "Docs", "Transcribe", "Image", "Batch"]);
  for (const t of m) {
    expect(t.right, `${t.label} overflows the strip`).toBeLessThanOrEqual(t.stripRight);
    expect(t.clipped, `${t.label} label is clipped`).toBe(false);
  }
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
  // The heads themselves are the structural content, and expanding
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
  // Sidebar carries the five category tabs; default-selected is
  // General. The presence of all five data-prefs-tab buttons is the
  // structural check.
  await expect(prefs.locator(".prefs-nav-item")).toHaveCount(5);
  await expect(prefs.locator(".prefs-nav-item.active")).toContainText("General");
});

test("Preferences Providers tab lists the named providers", async () => {
  ctx = await launchApp();
  const { window, electron } = ctx;
  const newWindowP = electron.waitForEvent("window", { timeout: 5_000 });
  await window.click("#navPrefs");
  const prefs = await newWindowP;
  await prefs.waitForLoadState("domcontentloaded");
  await prefs.click('.prefs-nav-item[data-prefs-tab="providers"]');
  const pane = prefs.locator('[data-prefs-pane="providers"]');
  await expect(pane).toBeVisible();
  // The tab has rendered once the intro copy is on screen.
  await expect(pane.locator(".prefs-hint").first()).toBeVisible();
  // One key row per named provider. A host with no encrypted storage (a
  // Linux box with no keyring) shows the notice instead and no rows.
  const notice = pane.locator(".prefs-empty-row", { hasText: "encrypted storage" });
  if (await notice.count() > 0) {
    await expect(notice).toBeVisible();
  } else {
    await expect(pane.locator(".prefs-row-provider")).toHaveCount(3);
  }
});

test("Model menu always offers a route to the Providers tab", async () => {
  ctx = await launchApp();
  const { window, electron } = ctx;
  await window.click("#pick");
  // The only mention of providers a user with local models would see.
  const row = window.locator(".mp-menu .mp-item", { hasText: /provider/i });
  await expect(row).toHaveCount(1);
  const newWindowP = electron.waitForEvent("window", { timeout: 5_000 });
  await row.click();
  const prefs = await newWindowP;
  await prefs.waitForLoadState("domcontentloaded");
  // Opening from that row lands on Providers, not on General's placeholder.
  await expect(prefs.locator(".prefs-nav-item.active")).toContainText("Providers");
  await expect(prefs.locator('[data-prefs-pane="providers"]')).toBeVisible();
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

test("Preferences Sidecar tab lists workspace paths and the runtime", async () => {
  ctx = await launchApp();
  const { electron, window } = ctx;
  await window.evaluate(() => window.cyllama.openPreferences());
  const prefs = await electron.waitForEvent("window");
  await prefs.waitForLoadState("domcontentloaded");
  await prefs.click('[data-prefs-tab="sidecar"]');

  const pane = prefs.locator("#prefsSidecar");
  // Workspace dirs, including the two the scripts work added.
  for (const label of ["models", "artifacts", "rag", "uploads", "workflows", "scripts"]) {
    await expect(pane).toContainText(label, { timeout: 15_000 });
  }
  // Runtime diagnostics: which interpreter runs the sidecar and its scripts.
  await expect(pane).toContainText("interpreter");
  await expect(pane).toContainText("packages");
  await expect(pane.locator(".prefs-copy").first()).toBeVisible();
});

test("Console opens in the chat pane and in a full-area pane", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  const drawer = window.locator("#console");
  await expect(drawer).toBeHidden();

  // Chat pane: the case that always worked.
  await window.click("#navConsole");
  await expect(drawer).toBeVisible();
  await window.click("#navConsole");
  await expect(drawer).toBeHidden();

  // Agents pane hides .main-col, which used to contain the console --
  // the toggle flipped [hidden] on an element inside a display:none
  // ancestor, so nothing appeared.
  await window.click("#navAgents");
  await expect(window.locator("#app")).toHaveAttribute("data-pane", "agents");
  await window.click("#navConsole");
  await expect(drawer).toBeVisible();
  await expect(window.locator("#agentsPane")).toBeVisible();
});

test("Scripts row lists a workspace script and streams its run", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  // Drop a script into the workspace the app just created. Nothing is
  // seeded on launch, so this is the only file the pane should list.
  const scriptsDir = path.join(userDataDir, "workspaces", "default", "scripts");
  fs.mkdirSync(scriptsDir, { recursive: true });
  fs.writeFileSync(
    path.join(scriptsDir, "smoke.py"),
    '"""Smoke script."""\nprint("hello from the script")\n',
  );

  await window.click("#navAgents");
  const row = window.locator("#agt-row-scripts");
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();
  await window.click("#scr-refresh");
  await window.click("#scr-item-smoke");
  await expect(window.locator('[data-script-id="smoke"] .scr-desc'))
    .toHaveText("Smoke script.");

  // Run from the script's own row, without scrolling to the Arguments
  // section at the bottom. The first run opens the source viewer, since
  // the file has not been read; Run in there starts it.
  await window.click("#scr-run-smoke");
  await window.click("#code-modal #cm-run");
  await expect(window.locator("#scr-log"))
    .toContainText("hello from the script", { timeout: 30_000 });
  await expect(window.locator("#agentsPaneDetail"))
    .toContainText("succeeded", { timeout: 30_000 });

  // The row is the only place a script starts: the Arguments section
  // carries the input and the status line, not a second Run.
  await expect(window.locator("#scr-run")).toHaveCount(0);
  await expect(window.locator("#scr-form")).toContainText("Arguments for smoke");
});

test("Scripts row installs and uninstalls a shipped script", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const sweep = path.join(userDataDir, "workspaces", "default", "scripts", "sweep.py");

  await window.click("#navAgents");
  await window.locator("#agt-row-scripts").click();
  // Listed but not installed: Install offered, no Run.
  await expect(window.locator("#scr-install-sweep")).toBeVisible({ timeout: 15_000 });
  await expect(window.locator("#scr-run-sweep")).toHaveCount(0);
  expect(fs.existsSync(sweep)).toBe(false);

  await window.click("#scr-install-sweep");
  await expect(window.locator("#scr-uninstall-sweep")).toBeVisible({ timeout: 15_000 });
  await expect(window.locator("#scr-run-sweep")).toBeVisible();
  expect(fs.existsSync(sweep)).toBe(true);

  await window.click("#scr-uninstall-sweep");
  await expect(window.locator("#scr-install-sweep")).toBeVisible({ timeout: 15_000 });
  expect(fs.existsSync(sweep)).toBe(false);
});

test("Scripts row shows a user's own script with no Uninstall", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const scriptsDir = path.join(userDataDir, "workspaces", "default", "scripts");
  fs.mkdirSync(scriptsDir, { recursive: true });
  fs.writeFileSync(path.join(scriptsDir, "mine.py"), '"""My own thing."""\n');

  await window.click("#navAgents");
  await window.locator("#agt-row-scripts").click();
  await window.click("#scr-refresh");
  await expect(window.locator("#scr-run-mine")).toBeVisible({ timeout: 15_000 });
  // Nothing offers to delete a file the app did not put there.
  await expect(window.locator("#scr-uninstall-mine")).toHaveCount(0);
  await expect(window.locator('[data-script-id="mine"] .scr-desc')).toHaveText("My own thing.");
});

test("Workflow row installs and uninstalls a shipped workflow", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const wc = path.join(userDataDir, "workspaces", "default", "workflows", "word_count.py");

  await window.click("#navAgents");
  await window.locator('[data-agent-type="agent-workflow"]').click();
  await expect(window.locator("#wf-install-word_count")).toBeVisible({ timeout: 15_000 });
  // Not installed: no Run, and no entry node on the row either.
  await expect(window.locator("#wf-run-word_count")).toHaveCount(0);
  expect(fs.existsSync(wc)).toBe(false);

  await window.click("#wf-install-word_count");
  await expect(window.locator("#wf-uninstall-word_count")).toBeVisible({ timeout: 15_000 });
  expect(fs.existsSync(wc)).toBe(true);

  await expect(window.locator("#wf-run-word_count")).toBeVisible();

  await window.click("#wf-uninstall-word_count");
  await expect(window.locator("#wf-install-word_count")).toBeVisible({ timeout: 15_000 });
  expect(fs.existsSync(wc)).toBe(false);
});

test("Workflow row Run is inert until required inputs are filled", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const dir = path.join(userDataDir, "workspaces", "default", "workflows");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "needs_text.py"), [
    "\x27\x27\x27Needs a text input.\x27\x27\x27",
    "from cyllama.agents.workflow import Workflow",
    "",
    "flow = Workflow()",
    'flow.add_node("count", lambda s: {"count": len(s.get("text", "").split())})',
    'flow.set_entry("count")',
    'flow.set_exit("count")',
    'flow.declare_inputs("text")',
    "",
  ].join("\n"));

  await window.click("#navAgents");
  await window.locator('[data-agent-type="agent-workflow"]').click();
  await window.click("#wf-refresh");
  await window.click("#wf-item-needs_text");
  const run = window.locator("#wf-run-needs_text");
  await expect(run).toBeDisabled();
  await window.fill('#wf-form input[data-state-key="text"]', "one two three");
  await expect(run).toBeEnabled();
  // Blank again: the row returns to inert without a re-render.
  await window.fill('#wf-form input[data-state-key="text"]', "   ");
  await expect(run).toBeDisabled();
});

test("Run on an unread script opens the source with the warning", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const dir = path.join(userDataDir, "workspaces", "default", "scripts");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "smoke.py"),
    '"""Smoke script."""\nprint("hello from the script")\n');

  await window.click("#navAgents");
  await window.locator("#agt-row-scripts").click();
  await window.click("#scr-refresh");
  await expect(window.locator("#scr-run-smoke")).toBeVisible({ timeout: 15_000 });

  // Run without having read it: the viewer opens instead of running.
  await window.click("#scr-run-smoke");
  const modal = window.locator("#code-modal");
  await expect(modal).toBeVisible();
  await expect(modal.locator("#cm-warning")).toContainText("not sandboxed");
  await expect(modal).toContainText("smoke.py");
  await expect(modal).toContainText(path.join(dir, "smoke.py"));
  // The code is shown, and highlighted.
  await expect(modal.locator(".cm-code")).toContainText("hello from the script");
  await expect(modal.locator(".cm-code .hljs-string").first()).toBeVisible();

  // Cancel: nothing ran.
  await modal.locator("button", { hasText: "Cancel" }).click();
  await expect(modal).toHaveCount(0);
  await expect(window.locator("#scr-log")).toHaveCount(0);

  // Run again, then approve from the dialog.
  await window.click("#scr-run-smoke");
  await window.click("#code-modal #cm-run");
  await expect(window.locator("#scr-log"))
    .toContainText("hello from the script", { timeout: 30_000 });

  // Read once: a later run goes straight through.
  await window.click("#scr-run-smoke");
  await expect(window.locator("#code-modal")).toHaveCount(0);
  await expect(window.locator("#agentsPaneDetail"))
    .toContainText("succeeded", { timeout: 30_000 });
});

test("View opens a script read-only, and counts as having read it", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const dir = path.join(userDataDir, "workspaces", "default", "scripts");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "smoke.py"),
    '"""Smoke script."""\nprint("hello from the script")\n');

  await window.click("#navAgents");
  await window.locator("#agt-row-scripts").click();
  await window.click("#scr-refresh");
  await window.click("#scr-view-smoke");

  const modal = window.locator("#code-modal");
  await expect(modal).toBeVisible();
  // Inspect mode: no warning, no Run.
  await expect(modal.locator("#cm-warning")).toHaveCount(0);
  await expect(modal.locator("#cm-run")).toHaveCount(0);
  await modal.locator("button", { hasText: "Close" }).click();
  await expect(modal).toHaveCount(0);

  // Having read it, Run does not re-open the viewer.
  await window.click("#scr-run-smoke");
  await expect(window.locator("#code-modal")).toHaveCount(0);
  await expect(window.locator("#agentsPaneDetail"))
    .toContainText("succeeded", { timeout: 30_000 });
});

test("View reads a shipped script before it is installed", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  await window.click("#navAgents");
  await window.locator("#agt-row-scripts").click();
  await expect(window.locator("#scr-install-sweep")).toBeVisible({ timeout: 15_000 });
  // Not installed, so there is no Run -- but it can still be read.
  await expect(window.locator("#scr-run-sweep")).toHaveCount(0);
  await window.click("#scr-view-sweep");
  const modal = window.locator("#code-modal");
  await expect(modal).toBeVisible();
  await expect(modal.locator(".cm-code")).toContainText("cyllama_desktop");
  await modal.locator("button", { hasText: "Close" }).click();
});

test("The selected file's path is shown with a Reveal button", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const dir = path.join(userDataDir, "workspaces", "default", "scripts");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "mine.py"), '"""Mine."""\n');

  await window.click("#navAgents");
  await window.locator("#agt-row-scripts").click();
  await window.click("#scr-refresh");
  await window.click("#scr-item-mine");
  const header = window.locator("#scr-file");
  await expect(header).toContainText("script file");
  await expect(header).toContainText(path.join(dir, "mine.py"));
  await expect(header.locator("button")).toHaveText("Reveal");
});

test("Workflow trace is capped and survives leaving the pane", async () => {
  ctx = await launchApp();
  const { window, userDataDir } = ctx;
  const dir = path.join(userDataDir, "workspaces", "default", "workflows");
  fs.mkdirSync(dir, { recursive: true });
  // The stub replays whatever the file puts in `_script`, so this emits
  // 500 node events -- more than the 400-row cap.
  fs.writeFileSync(path.join(dir, "noisy.py"), [
    "\x27\x27\x27Emits more events than the pane keeps.\x27\x27\x27",
    "from cyllama.agents.workflow import Workflow",
    "",
    "flow = Workflow()",
    'flow.add_node("n", lambda s: {"n": 1})',
    'flow.set_entry("n")',
    'flow.set_exit("n")',
    'flow._script = [("NODE_END", "line %d" % i, {"node": "n"}) for i in range(500)]',
    "",
  ].join("\n"));

  await window.click("#navAgents");
  await window.locator('[data-agent-type="agent-workflow"]').click();
  await window.click("#wf-refresh");
  await window.click("#wf-item-noisy");
  await window.click("#wf-run-noisy");
  await window.click("#code-modal #cm-run");

  const rows = window.locator("#wf-events > div");
  await expect.poll(() => rows.count(), { timeout: 30_000 }).toBe(400);

  // Leave the pane and come back: the trace is rebuilt from state
  // rather than rendering empty.
  await window.locator('[data-agent-type="agent"]').click();
  await window.locator('[data-agent-type="agent-workflow"]').click();
  await expect(window.locator("#wf-events > div")).toHaveCount(400);
});

test("A wide markdown table scrolls, and a huge image is clamped", async () => {
  ctx = await launchApp();
  const { window } = ctx;
  // Render through the same marked path the chat stream uses.
  const m = await window.evaluate(async () => {
    const at = document.createElement("div");
    at.className = "asst-text";
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="60">'
      + '<rect width="2400" height="60" fill="#4f46e5"/></svg>';
    // Twelve columns of real words: wide enough to overflow any
    // plausible message column, so the assertion is not window-size
    // dependent.
    const cols = ["variant", "linux", "windows", "macos", "runtime",
      "registries", "notes", "status", "owner", "eta", "installer", "verified"];
    const row = ["cuda12", "supported", "supported", "unsupported",
      "CUDA 12 plus cuBLAS", "CPU, CUDA", "needs a driver", "shipped",
      "unassigned", "none", "nsis per backend", "not yet"];
    at.innerHTML = window.marked ? marked.parse([
      "| " + cols.join(" | ") + " |",
      "|" + cols.map(() => "---").join("|") + "|",
      "| " + row.join(" | ") + " |",
      "",
      "![wide](data:image/svg+xml;base64," + btoa(svg) + ")",
    ].join("\n")) : "";
    document.getElementById("log").appendChild(at);
    const img = at.querySelector("img");
    await new Promise((res) => {
      if (img.complete && img.naturalWidth) return res();
      img.addEventListener("load", res, { once: true });
      img.addEventListener("error", res, { once: true });
      setTimeout(res, 2000);
    });
    const t = at.querySelector("table");
    return {
      column: at.clientWidth,
      tableScrolls: t.scrollWidth > t.clientWidth,
      tableWidth: t.clientWidth,
      imgNatural: img.naturalWidth,
      imgRendered: img.clientWidth,
      pageScrollsSideways:
        document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
  // The table overflows into its own scroll area rather than squeezing
  // its columns, and does not widen the message column.
  expect(m.tableScrolls).toBe(true);
  expect(m.tableWidth).toBeLessThanOrEqual(m.column);
  // A 2400px image is clamped to the column.
  expect(m.imgNatural).toBe(2400);
  expect(m.imgRendered).toBeLessThanOrEqual(m.column);
  // Neither pushes the window sideways.
  expect(m.pageScrollsSideways).toBe(false);
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
