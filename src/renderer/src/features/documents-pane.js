// Documents sidebar view (Phase 4). Mounts the RAG collections UI
// (list/create/delete + ingest job + streaming query) into the
// ``#docsBody`` slot inside the Documents sidebar view.
//
// Sidebar-view switching is handled in main.js via the ``setSidebarView``
// helper; ``show()`` here is what main.js calls when the Documents view
// becomes active, and it refreshes the collections list.

import { listCollections, createCollection, deleteCollection, ingest, query } from "../lib/rag.js";
import { listModels } from "../lib/models.js";

const state = {
  collections: [],
  models: [],
  activeId: null,
  mode: "browse", // "browse" | "create"
  queryAbort: null,
};

function fmtBytes(n) {
  if (!Number.isFinite(n)) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${u[i]}`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// Pull the most useful filesystem-ish path out of a source's metadata.
// cyllama's loaders write the source path under varying keys depending
// on which loader fired; cover the obvious ones.
function sourcePath(s) {
  const md = (s && s.metadata) || {};
  for (const k of ["source", "path", "file", "filename"]) {
    const v = md[k];
    if (typeof v === "string" && v) return v;
  }
  return "";
}

function makeSourceRow(s, i) {
  const path = sourcePath(s);
  const fileName = path ? path.split(/[\\/]/).pop() : "";
  const text = s.text || "";

  // Click anywhere on the row toggles expand. Reveal button stops
  // propagation so it doesn't accidentally collapse on click.
  const row = el("div", {
    class: "dp-source",
    role: "button",
    tabindex: "0",
    "aria-expanded": "false",
    title: "Click to expand",
  });

  const head = el("div", { class: "dp-source-head" },
    el("span", { class: "dp-source-idx" }, `[${i + 1}]`),
    el("span", { class: "dp-source-score" }, `score ${Number(s.score || 0).toFixed(3)}`),
    fileName ? el("span", { class: "dp-source-file mono" }, fileName) : null,
  );
  const textEl = el("div", { class: "dp-source-text dp-source-collapsed" }, text);
  row.appendChild(head);
  row.appendChild(textEl);

  if (path && window.cyllama && typeof window.cyllama.revealItem === "function") {
    const reveal = el("button", {
      type: "button",
      class: "btn-mini",
      title: `Reveal ${path}`,
      onclick: (e) => {
        e.stopPropagation();
        window.cyllama.revealItem(path);
      },
    }, "Reveal");
    head.appendChild(reveal);
  }

  function toggle() {
    const expanded = row.getAttribute("aria-expanded") === "true";
    row.setAttribute("aria-expanded", expanded ? "false" : "true");
    textEl.classList.toggle("dp-source-collapsed", expanded);
    row.title = expanded ? "Click to expand" : "Click to collapse";
  }
  row.addEventListener("click", toggle);
  row.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
  });

  return row;
}

function activeCollection() {
  return state.collections.find((c) => c.id === state.activeId) || null;
}

async function refresh() {
  try {
    const [r, m] = await Promise.all([listCollections(), listModels()]);
    state.collections = r.collections || [];
    state.models = m.models || [];
  } catch (e) {
    state.collections = [];
    state.models = [];
    console.error("documents refresh:", e);
  }
  if (state.activeId && !state.collections.find((c) => c.id === state.activeId)) {
    state.activeId = null;
  }
  if (!state.activeId && state.collections.length) state.activeId = state.collections[0].id;
  redraw();
}

function emitCollectionsChanged() {
  try { window.dispatchEvent(new CustomEvent("rag:collections-changed")); }
  catch (_) { /* no DOM in tests */ }
}

function modelDropdown({ value, placeholder }) {
  const sel = el("select", { class: "dp-select" });
  sel.appendChild(el("option", { value: "", disabled: true, selected: !value }, placeholder));
  for (const m of state.models) {
    const opt = el("option", { value: m.path, title: m.path },
      `${m.name} · ${fmtBytes(m.size)}`);
    if (value && m.path === value) opt.selected = true;
    sel.appendChild(opt);
  }
  return sel;
}

function showStatus(text, kind = "info") {
  const s = document.querySelector("#docsBody .dp-status");
  if (!s) return;
  s.textContent = text;
  s.dataset.kind = kind;
}

// ---- Top picker bar -------------------------------------------------------

function renderHeader() {
  const sel = el("select", { class: "dp-select" });
  if (state.collections.length === 0) {
    sel.appendChild(el("option", { value: "", disabled: true, selected: true }, "No collections — create one"));
    sel.disabled = true;
  } else {
    for (const c of state.collections) {
      const opt = el("option", { value: c.id }, `${c.name}  ·  ${c.chunk_count} chunks`);
      if (c.id === state.activeId) opt.selected = true;
      sel.appendChild(opt);
    }
  }
  sel.addEventListener("change", () => { state.activeId = sel.value; redraw(); });

  const newBtn = el("button", { type: "button", class: "btn" }, "New");
  newBtn.addEventListener("click", () => { state.mode = "create"; redraw(); });

  const delBtn = el("button", {
    type: "button", class: "btn dp-del",
    disabled: !state.activeId,
  }, "Delete");
  delBtn.addEventListener("click", async () => {
    const c = activeCollection();
    if (!c) return;
    if (!confirm(`Delete collection "${c.name}"? This removes its sqlite store.`)) return;
    try {
      await deleteCollection(c.id);
      state.activeId = null;
      await refresh();
      emitCollectionsChanged();
    } catch (e) {
      showStatus(`Delete failed: ${e.message}`, "err");
    }
  });

  return el("div", { class: "dp-header" },
    el("span", { class: "dp-header-label" }, "Collection"),
    sel, newBtn, delBtn,
  );
}

// ---- Create-collection inline form ----------------------------------------

function renderCreateForm() {
  const nameInput = el("input", { type: "text", class: "dp-input", placeholder: "Collection name" });
  const embedSel = modelDropdown({ value: "", placeholder: "Pick embedding model..." });
  const cancelBtn = el("button", { type: "button", class: "btn" }, "Cancel");
  const createBtn = el("button", { type: "button", class: "btn primary" }, "Create");

  cancelBtn.addEventListener("click", () => { state.mode = "browse"; redraw(); });
  createBtn.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    const embed = embedSel.value;
    if (!name || !embed) {
      showStatus("Name and embedding model are required.", "err");
      return;
    }
    createBtn.disabled = true;
    try {
      const rec = await createCollection({ name, embedding_model_path: embed });
      state.activeId = rec.id;
      state.mode = "browse";
      await refresh();
      emitCollectionsChanged();
    } catch (e) {
      showStatus(`Create failed: ${e.message}`, "err");
      createBtn.disabled = false;
    }
  });

  return el("section", { class: "dp-section" },
    el("h3", {}, "New collection"),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Name"),
      nameInput,
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Embedding model"),
      embedSel,
    ),
    el("div", { class: "dp-row dp-actions" }, cancelBtn, createBtn),
  );
}

// ---- Ingest section -------------------------------------------------------

function renderIngest(coll) {
  const pathsInput = el("textarea", {
    class: "dp-textarea",
    rows: 3,
    placeholder: "Absolute paths, one per line (file or directory)",
  });
  const globInput = el("input", { type: "text", class: "dp-input", placeholder: "**/*", value: "**/*" });
  const button = el("button", { type: "button", class: "btn primary" }, "Ingest");
  const log = el("pre", { class: "dp-ingest-log" });

  button.addEventListener("click", async () => {
    const paths = pathsInput.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!paths.length) {
      log.textContent = "Add at least one path.\n";
      return;
    }
    log.textContent = "";
    button.disabled = true;
    try {
      const job = await ingest({
        collection_id: coll.id,
        paths,
        glob: globInput.value || "**/*",
      });
      job.onEvent((ev) => {
        if (ev.type === "log") log.textContent += ev.message + "\n";
        else if (ev.type === "progress") {
          const v = ev.value != null ? `${Math.round(ev.value * 100)}%` : "";
          log.textContent += `progress ${v} (${ev.added}/${ev.total})\n`;
        } else if (ev.type === "error") log.textContent += `error: ${ev.message}\n`;
        else if (ev.type === "result") log.textContent += `done: ${ev.result.documents} docs, ${ev.result.chunks} chunks\n`;
        else if (ev.type === "done") {
          button.disabled = false;
          refresh();
        }
        log.scrollTop = log.scrollHeight;
      });
    } catch (e) {
      log.textContent += `ingest failed: ${e.message}\n`;
      button.disabled = false;
    }
  });

  return el("section", { class: "dp-section" },
    el("h3", {}, "Ingest"),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Paths"),
      pathsInput,
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Glob"),
      globInput,
    ),
    el("div", { class: "dp-row dp-actions" }, button),
    log,
  );
}

// ---- Query section --------------------------------------------------------

function renderQuery(coll) {
  const lastGen = localStorage.getItem("rag_last_gen_model") || "";
  const genSel = modelDropdown({ value: lastGen, placeholder: "Pick generation model..." });
  const qInput = el("textarea", {
    class: "dp-textarea",
    rows: 3,
    placeholder: "Ask a question about this collection...",
  });
  const askBtn = el("button", { type: "button", class: "btn primary" }, "Ask");
  const stopBtn = el("button", { type: "button", class: "btn", hidden: true }, "Stop");
  const sourcesBlock = el("div", { class: "dp-sources" });
  const answerBlock = el("div", { class: "dp-answer" });

  function reset() {
    sourcesBlock.replaceChildren();
    answerBlock.textContent = "";
  }

  async function run() {
    const gen = genSel.value;
    const q = qInput.value.trim();
    if (!gen) { answerBlock.textContent = "Pick a generation model."; return; }
    if (!q)   { answerBlock.textContent = "Ask a question."; return; }
    localStorage.setItem("rag_last_gen_model", gen);

    reset();
    askBtn.hidden = true; stopBtn.hidden = false;
    state.queryAbort = new AbortController();

    let acc = "";
    try {
      await query({
        collection_id: coll.id,
        generation_model_path: gen,
        question: q,
      }, {
        signal: state.queryAbort.signal,
        onSources: ({ sources }) => {
          sourcesBlock.replaceChildren(
            el("h4", {}, `Sources (${sources.length})`),
            ...sources.map((s, i) => makeSourceRow(s, i)),
          );
        },
        onToken: (t) => {
          acc += t;
          answerBlock.textContent = acc;
        },
      });
    } catch (e) {
      if (e.name !== "AbortError") {
        answerBlock.textContent += `\n\n[error: ${e.message}]`;
      }
    } finally {
      askBtn.hidden = false; stopBtn.hidden = true;
      state.queryAbort = null;
    }
  }

  askBtn.addEventListener("click", run);
  stopBtn.addEventListener("click", () => {
    if (state.queryAbort) state.queryAbort.abort();
  });

  return el("section", { class: "dp-section" },
    el("h3", {}, "Query"),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Generation model"),
      genSel,
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Question"),
      qInput,
    ),
    el("div", { class: "dp-row dp-actions" }, askBtn, stopBtn),
    sourcesBlock,
    answerBlock,
  );
}

// ---- Render --------------------------------------------------------------

function redraw() {
  const body = document.getElementById("docsBody");
  if (!body) return;
  const status = body.querySelector(".dp-status") || el("div", { class: "dp-status" });
  body.replaceChildren(renderHeader(), status);

  if (state.mode === "create") {
    body.appendChild(renderCreateForm());
    return;
  }

  const coll = activeCollection();
  if (!coll) {
    body.appendChild(el("div", { class: "dp-empty" },
      "No collection selected. Click ", el("strong", {}, "New"), " to create one."));
    return;
  }

  body.appendChild(el("div", { class: "dp-meta mono" },
    coll.embedding_model_path,
    el("br"),
    `${coll.doc_count} docs · ${coll.chunk_count} chunks`,
  ));
  body.appendChild(renderIngest(coll));
  body.appendChild(renderQuery(coll));
}

// ---- View lifecycle -------------------------------------------------------

// Called by ``main.js`` when the Documents sidebar view becomes active.
// Idempotent: refreshing every time keeps the collection list current
// after external changes (deletes / ingests from prior visits).
export async function show() {
  if (!document.getElementById("docsBody")) return;
  await refresh();
}

// Stop any in-flight streaming query when the view is hidden so the
// stream doesn't keep accumulating into a hidden DOM node.
export function hide() {
  if (state.queryAbort) {
    try { state.queryAbort.abort(); } catch (_) {}
    state.queryAbort = null;
  }
}
