// Playwright config for the per-pane e2e smoke suite.
//
// The suite boots Electron via @playwright/test's electron launcher
// against a stubbed cyllama sidecar (tests/e2e/sidecar_launcher.py).
// No bundled python-build-standalone env required: a system python is
// fine because the launcher's only Python dependencies are FastAPI +
// uvicorn (already in python-sidecar/pyproject.toml) plus the conftest
// cyllama stub.

const { defineConfig } = require("@playwright/test");
const path = require("path");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  testMatch: /.*\.spec\.js/,
  // Each test boots Electron + the sidecar; cold-start is the
  // dominant cost. Generous timeout absorbs CI cold caches.
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Electron can't be parallelized across the same userData dir; run
  // serially. Each test gets its own userData via
  // ELECTRON_USER_DATA_DIR (set in the helper).
  workers: 1,
  fullyParallel: false,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    trace: "on-first-retry",
  },
});
