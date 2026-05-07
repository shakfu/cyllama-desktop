// Slash-command registry + parsing.
//
// Pure data + parsing helpers. The runtime registry (with `run`
// handlers) lives in main.js so handlers stay colocated with the chat
// state they touch. See docs/slash-commands.md for the design.

// Match shape: optional leading whitespace, "/", a name (letters,
// digits, underscore, hyphen; must start with a letter), then either
// end-of-string or a separator + body.
const CMD_RE = /^\s*\/([A-Za-z][A-Za-z0-9_-]*)\b\s*([\s\S]*)$/;

// Match shape for in-progress typing (autocomplete trigger): a slash
// with an optional partial name and nothing after it yet.
const TYPING_RE = /^\s*\/([A-Za-z][A-Za-z0-9_-]*)?$/;

// Parse a prompt against the set of registered command names. Returns
// { name, body, raw } if the first token is a known command, else null.
// Unknown slashes pass through as plain chat turns by design.
export function parse(prompt, names) {
  const m = prompt.match(CMD_RE);
  if (!m) return null;
  const name = m[1].toLowerCase();
  if (!names.includes(name)) return null;
  return { name, body: m[2].trim(), raw: prompt };
}

// Detect "user is mid-typing a slash command" for autocomplete. Returns
// the partial prefix (without the leading slash) or null.
export function typingPrefix(prompt) {
  const m = prompt.match(TYPING_RE);
  if (!m) return null;
  return (m[1] || "").toLowerCase();
}

export function matches(prefix, names) {
  return names.filter((n) => n.startsWith(prefix));
}

// Longest common prefix of an array of strings.
export function lcp(strs) {
  if (!strs.length) return "";
  let p = strs[0];
  for (const s of strs) {
    while (!s.startsWith(p)) p = p.slice(0, -1);
    if (!p) return "";
  }
  return p;
}
