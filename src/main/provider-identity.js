// Compat-endpoint identity and URL policy for the JS side. The sidecar's
// providers.py holds the Python copy; tests/fixtures/provider_identity.json
// pins both to the same answers. No Electron imports, so node can load it.

// URL.hostname keeps IPv6 brackets; Python's urlparse strips them.
const LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

function parse(url) {
  try { return new URL(url); } catch (_) { return null; }
}

// https anywhere, http only for loopback. Mirrors providers.endpoint_acceptable.
function endpointAcceptable(url) {
  const u = parse(url);
  if (!u) return false;
  if (u.protocol === "https:") return !!u.hostname;
  if (u.protocol === "http:") return LOOPBACK_HOSTS.includes(u.hostname);
  return false;
}

// Mirrors Provider.needs_key for a compat endpoint.
function endpointNeedsKey(url) {
  const u = parse(url);
  return !u || !LOOPBACK_HOSTS.includes(u.hostname);
}

// Mirrors providers._normalize_account_suffix.
function normalizeAccountSuffix(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

module.exports = { endpointAcceptable, endpointNeedsKey, normalizeAccountSuffix };
