// The main process's endpoint rules against the fixture tests/test_providers.py
// checks the sidecar with. Plain node: no Electron launch.

const { test, expect } = require("@playwright/test");
const identity = require("../../src/main/provider-identity.js");
const fixture = require("../fixtures/provider_identity.json");

for (const [name, suffix] of fixture.account_suffix) {
  test(`account suffix for ${JSON.stringify(name)}`, () => {
    expect(identity.normalizeAccountSuffix(name)).toBe(suffix);
  });
}

for (const c of fixture.endpoints) {
  test(`endpoint policy for ${c.url}`, () => {
    expect(identity.endpointAcceptable(c.url)).toBe(c.acceptable);
    expect(identity.endpointNeedsKey(c.url)).toBe(c.needs_key);
  });
}
