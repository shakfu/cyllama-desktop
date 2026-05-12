// Per-call agent configuration modal.
//
// Opened by /agent-strict, /agent-contract, /agent-reflect just before
// the slash handler kicks off the run. Pre-filled with defaults from
// the Agents pane (so an Enter-press runs with those defaults
// unchanged); the user can tweak any field for this one invocation.
//
// Schema-driven so we don't repeat boilerplate per slash. A caller
// passes the task string, a list of field descriptors, and a
// resolve/reject pair; the modal returns the user's chosen values on
// submit or rejects (caller cancels the run) on dismissal.
//
// Field shape:
//   { key: string, label: string, type: "text" | "number" | "select" |
//     "checkbox" | "textarea", default: any, options?: string[],
//     min?: number, max?: number, placeholder?: string, hint?: string }

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
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


// Build an input element for one field. Returns the element plus a
// reader function that pulls the current value off it.
function buildField(field) {
  const v = field.default;
  switch (field.type) {
    case "checkbox": {
      const input = el("input", {
        type: "checkbox", id: `am-${field.key}`, checked: !!v,
      });
      return [input, () => input.checked];
    }
    case "number": {
      const input = el("input", {
        type: "number", class: "dp-input", id: `am-${field.key}`,
        min: field.min, max: field.max, step: 1,
        value: v != null ? String(v) : "",
      });
      return [input, () => {
        const n = parseInt(input.value, 10);
        if (!Number.isFinite(n)) return field.default;
        if (field.min != null && n < field.min) return field.min;
        if (field.max != null && n > field.max) return field.max;
        return n;
      }];
    }
    case "select": {
      const input = el("select", { class: "dp-select", id: `am-${field.key}` });
      for (const o of field.options || []) {
        const opt = el("option", { value: o }, o);
        if (o === v) opt.selected = true;
        input.appendChild(opt);
      }
      return [input, () => input.value];
    }
    case "textarea": {
      const input = el("textarea", {
        class: "dp-input", id: `am-${field.key}`,
        rows: field.rows || 3,
        placeholder: field.placeholder || "",
      });
      input.value = v != null ? String(v) : "";
      return [input, () => input.value];
    }
    case "text":
    default: {
      const input = el("input", {
        type: "text", class: "dp-input", id: `am-${field.key}`,
        value: v != null ? String(v) : "",
        placeholder: field.placeholder || "",
      });
      return [input, () => input.value];
    }
  }
}


// Open the modal. Returns a Promise that resolves with the field-value
// map on submit, or rejects on cancel/escape so the caller can drop
// the pending run cleanly. When ``task`` is supplied, a Task textarea
// is rendered at the top and its (possibly edited) content lands on
// the resolved object under the ``task`` key -- callers should use
// ``values.task`` rather than the original task argument so user
// refinements in the modal take effect.
export function openAgentModal({ title, hint, task, fields }) {
  return new Promise((resolve, reject) => {
    const readers = {};

    const body = el("form", { class: "agent-modal-form" });
    if (task != null) {
      // Editable task: rendered as a textarea so multi-line refinements
      // (rephrase, expand, add constraints) work in place. Pre-filled
      // with whatever the user typed after the slash.
      const taskInput = el("textarea", {
        class: "dp-input agent-modal-task-input", id: "am-task", rows: 3,
        placeholder: "Refine the task before running.",
      });
      taskInput.value = String(task);
      readers["task"] = () => taskInput.value;
      body.appendChild(el("div", { class: "agent-modal-task" },
        el("label", { class: "ag-label", for: "am-task" }, "Task"),
        taskInput,
      ));
    }
    for (const f of fields) {
      const [input, read] = buildField(f);
      readers[f.key] = read;
      const row = el("div", { class: "agent-modal-row" },
        el("label", { class: "ag-label", for: `am-${f.key}` }, f.label),
        input,
      );
      if (f.hint) {
        row.appendChild(el("div", { class: "ag-tool-hint" }, f.hint));
      }
      body.appendChild(row);
    }

    const cancelBtn = el("button", { type: "button", class: "btn" }, "Cancel");
    const runBtn = el("button", { type: "submit", class: "btn btn-primary" }, "Run");
    body.appendChild(el("div", { class: "agent-modal-actions" }, cancelBtn, runBtn));

    const dialog = el("dialog", { class: "agent-modal" },
      el("header", { class: "agent-modal-head" },
        el("h2", {}, title),
        hint ? el("p", { class: "agent-modal-hint" }, hint) : null,
      ),
      body,
    );

    let settled = false;
    const cleanup = () => {
      if (dialog.parentNode) dialog.parentNode.removeChild(dialog);
    };
    const doResolve = (values) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(values);
    };
    const doReject = (reason) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(reason || new Error("modal cancelled"));
    };

    body.addEventListener("submit", (e) => {
      e.preventDefault();
      const values = {};
      for (const [k, read] of Object.entries(readers)) values[k] = read();
      doResolve(values);
    });
    cancelBtn.addEventListener("click", () => doReject(new Error("cancelled")));
    dialog.addEventListener("cancel", (e) => {
      // Esc fires "cancel" before "close"; reject so the caller drops
      // the run rather than running with stale defaults.
      e.preventDefault();
      doReject(new Error("cancelled"));
    });
    // Click on the backdrop (the dialog itself, outside the form) closes.
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) doReject(new Error("dismissed"));
    });

    document.body.appendChild(dialog);
    // Defer to next tick so the dialog is mounted before showModal.
    requestAnimationFrame(() => {
      dialog.showModal();
      // Focus the first input so Enter-from-keyboard submits immediately
      // for the common "accept defaults" case.
      const firstInput = body.querySelector("input,select,textarea");
      if (firstInput && typeof firstInput.focus === "function") {
        firstInput.focus();
        if (firstInput.select) firstInput.select();
      }
    });
  });
}
