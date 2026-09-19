// Providers tab: API keys for the three named providers, plus the user's own
// OpenAI-compatible endpoints.
//
// No key is ever read back. The main process reports which accounts hold one
// and this tab renders a boolean; the value goes straight from the input to
// safeStorage and from there to the sidecar over loopback.

import { NAMED_PROVIDERS } from "../shared/provider-identity.js";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// Loopback call to the running sidecar. The Preferences window reaches it
// the same way the Sidecar tab does: port + token from ``sidecar:info``.
async function sidecarJson(path, options = {}) {
  const info = await window.cyllama.getSidecarInfo();
  if (!info) throw new Error("sidecar not running");
  const res = await fetch(`http://127.0.0.1:${info.port}${path}`, {
    ...options,
    headers: { authorization: `Bearer ${info.token}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function fmtTokens(n) {
  const v = Number(n) || 0;
  if (v < 1000) return String(v);
  if (v < 1000000) return `${(v / 1000).toFixed(v < 10000 ? 1 : 0)}k`;
  return `${(v / 1000000).toFixed(1)}M`;
}

// Token totals per account and model. Tokens rather than money: a price
// table would go stale, and vary by tier and by cached input.
async function renderUsage(host, status) {
  host.appendChild(el("h3", { class: "prefs-section" }, "Usage"));
  host.appendChild(el("p", { class: "prefs-hint" },
    "Tokens sent to and received from each provider, counted per model. ",
    "Scripts and workflows can use a configured key, so this is where their ",
    "spend shows up."));

  const rows = el("div", { class: "prefs-rows" });
  host.appendChild(rows);

  let totals = [];
  try {
    totals = (await sidecarJson("/providers/usage")).totals || [];
  } catch (e) {
    rows.appendChild(el("div", { class: "prefs-empty-row" },
      `Usage unavailable: ${e.message}`));
    return;
  }

  if (!totals.length) {
    rows.appendChild(el("div", { class: "prefs-empty-row" },
      "No provider calls recorded yet."));
    return;
  }

  for (const t of totals) {
    const row = el("div", { class: "prefs-row prefs-row-provider" });
    row.appendChild(el("div", { class: "prefs-row-label" }, t.account));
    row.appendChild(el("div", { class: "prefs-row-path mono" }, t.model));
    row.appendChild(el("div", { class: "prefs-row-path" },
      `${t.calls} call${t.calls === 1 ? "" : "s"} · `
      + `${fmtTokens(t.prompt_tokens)} in · ${fmtTokens(t.completion_tokens)} out`));
    rows.appendChild(row);
  }

  host.appendChild(el("div", { class: "prefs-actions" },
    el("button", {
      type: "button", class: "btn-mini",
      onclick: async () => {
        try {
          const r = await sidecarJson("/providers/usage", { method: "DELETE" });
          status.dataset.kind = "ok";
          status.textContent = `usage cleared (${r.deleted} rows)`;
          await render();
        } catch (e) {
          status.dataset.kind = "err";
          status.textContent = `failed: ${e.message}`;
        }
      },
    }, "Clear usage")));
}

// Created once and re-attached by each render. Actions report here and then
// re-render, so a per-render element dropped every success message.
const status = el("div", { class: "prefs-status" });

export async function render() {
  const host = document.getElementById("prefsProviders");
  if (!host) return;
  host.replaceChildren();

  let state = { available: false, configured: [], endpoints: [] };
  try { state = await window.cyllama.providers.list({ probe: true }); } catch { /* no bridge */ }
  // Each carries ``account`` and ``needs_key``, derived by the main process.
  const endpoints = state.endpoints || [];

  host.appendChild(el("p", { class: "prefs-hint" },
    "Keys are stored with the operating system's encrypted storage and sent ",
    "only to the sidecar, which makes every provider call. A chat against a ",
    "provider leaves this machine; local models still do not."));

  if (!state.available) {
    // No OS keyring, or a Linux session whose only backend derives its key
    // from a password compiled into Chromium. Saving under a publicly known
    // key while the UI implied otherwise would be worse than declining.
    host.appendChild(el("div", { class: "prefs-empty-row" },
      "This system has no encrypted storage available, so provider keys ",
      "cannot be saved. On Linux this means no desktop keyring is running -- ",
      "the fallback would encrypt with a key anyone can look up."));
    return;
  }

  function keyRow(label, account, hint, opts = {}) {
    const configured = (state.configured || []).includes(account);
    const row = el("div", { class: "prefs-row prefs-row-provider" });
    row.appendChild(el("div", { class: "prefs-row-label" }, label));
    let statusText = configured ? "key set" : hint || "no key";
    if (!configured && opts.keyless) statusText = `${hint} · no key needed`;
    row.appendChild(el("div", { class: "prefs-row-path mono" }, statusText));

    const input = el("input", {
      type: "password",
      class: "prefs-input",
      // A local server started with its own token still takes one, so the
      // field stays; it is just not a precondition for using the endpoint.
      placeholder: configured ? "replace key"
        : opts.keyless ? "token, if the server needs one" : "paste key",
      autocomplete: "off",
      spellcheck: "false",
    });
    row.appendChild(input);

    row.appendChild(el("button", {
      type: "button", class: "btn-mini",
      onclick: async () => {
        const value = input.value.trim();
        if (!value) {
          status.dataset.kind = "info";
          status.textContent = "paste a key first";
          return;
        }
        try {
          await window.cyllama.providers.setKey(account, value);
          input.value = "";
          status.dataset.kind = "ok";
          status.textContent = `${label}: key saved`;
          await render();
        } catch (e) {
          status.dataset.kind = "err";
          status.textContent = `failed: ${e.message}`;
        }
      },
    }, "Save"));

    if (configured) {
      row.appendChild(el("button", {
        type: "button", class: "btn-mini",
        onclick: async () => {
          try {
            await window.cyllama.providers.deleteKey(account);
            status.dataset.kind = "ok";
            status.textContent = `${label}: key removed`;
            await render();
          } catch (e) {
            status.dataset.kind = "err";
            status.textContent = `failed: ${e.message}`;
          }
        },
      }, "Remove"));
    }
    return row;
  }

  const named = el("div", { class: "prefs-rows" });
  for (const p of NAMED_PROVIDERS) named.appendChild(keyRow(p.label, p.account, p.hint));
  host.appendChild(named);

  host.appendChild(el("h3", { class: "prefs-section" }, "OpenAI-compatible endpoints"));
  host.appendChild(el("p", { class: "prefs-hint" },
    "Anything speaking /v1/chat/completions: Ollama, LM Studio, Groq, ",
    "Together, a llama.cpp server. https is required except on localhost. ",
    "Each endpoint keeps its own key and model list, keyed by name. An ",
    "endpoint on localhost needs no key -- those servers authenticate ",
    "nothing, and it is usable as soon as you add it."));

  const custom = el("div", { class: "prefs-rows" });
  if (!endpoints.length) {
    custom.appendChild(el("div", { class: "prefs-empty-row" },
      "No endpoints yet."));
  }
  for (const e of endpoints) {
    // A local server that authenticates nothing needs no credential, so the
    // row says so instead of asking the user to invent one.
    const row = keyRow(e.name, e.account, e.base_url, { keyless: !e.needs_key });
    row.appendChild(el("button", {
      type: "button", class: "btn-mini",
      onclick: async () => {
        try {
          await window.cyllama.settings.set({
            provider_endpoints: endpoints.filter((x) => x.name !== e.name),
          });
          // The key is deliberately left in place: re-adding the same name
          // picks it up again. "Remove" on the key row is what deletes it.
          status.dataset.kind = "ok";
          status.textContent = `${e.name}: endpoint removed`;
          await render();
        } catch (err) {
          status.dataset.kind = "err";
          status.textContent = `failed: ${err.message}`;
        }
      },
    }, "Delete endpoint"));
    custom.appendChild(row);
  }
  host.appendChild(custom);

  const nameInput = el("input", { type: "text", class: "prefs-input", placeholder: "name" });
  const urlInput = el("input", {
    type: "text", class: "prefs-input prefs-input-wide",
    placeholder: "https://host/v1",
  });
  const addBtn = el("button", {
    type: "button", class: "btn",
    onclick: async () => {
      const name = nameInput.value.trim();
      const baseUrl = urlInput.value.trim();
      if (!name || !baseUrl) {
        status.dataset.kind = "info";
        status.textContent = "name and URL are both required";
        return;
      }
      const next = [...endpoints, { name, base_url: baseUrl }];
      try {
        await window.cyllama.settings.set({ provider_endpoints: next });
        // The main process drops an endpoint whose URL is not acceptable or
        // whose name collides, so check rather than assume it landed.
        const listed = await window.cyllama.providers.list();
        const added = (listed.endpoints || []).find((x) => x.name === name);
        if (!added) {
          status.dataset.kind = "err";
          status.textContent = "rejected: URL must be https (or http on localhost), "
            + "and the name must be unique";
          return;
        }
        nameInput.value = "";
        urlInput.value = "";
        status.dataset.kind = "ok";
        status.textContent = added.needs_key
          ? `${name}: endpoint added. Save a key for it in the list above.`
          : `${name}: endpoint added. Local, so no key is needed -- pick it in the model menu.`;
        await render();
      } catch (e) {
        status.dataset.kind = "err";
        status.textContent = `failed: ${e.message}`;
      }
    },
  }, "Add endpoint");

  host.appendChild(el("div", { class: "prefs-actions" }, nameInput, urlInput, addBtn));
  await renderUsage(host, status);
  host.appendChild(status);
}
