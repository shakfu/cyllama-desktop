// Read-only source viewer for workspace scripts and workflows.
//
// These files are unrestricted Python. Rather than describe that in a
// prose warning and ask the user to agree to it, the pane shows the
// code: Run on a file the user has not looked at opens this dialog with
// the warning attached, and the run starts from here.
//
// Highlighting uses highlight.js core with the Python grammar only,
// bundled by esbuild. A hand-rolled lexer was the alternative and is
// the wrong trade here: the point of the view is that the user can
// judge the code, and a lexer that mis-reads a string as a comment
// would work against exactly that.

import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";

hljs.registerLanguage("python", python);

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

export const CODE_WARNING =
  "Warning: Python scripts are an advanced feature. They are not "
  + "sandboxed, and have the full power of Python. Make sure you "
  + "understand the code before running it.";

// Resolves true when the user runs from the dialog, false otherwise.
// `mode: "inspect"` shows a single Close button and no warning;
// `mode: "confirm"` shows the warning plus Cancel / Run.
export function openCodeModal({ id, path, source, error, mode = "inspect" }) {
  return new Promise((resolve) => {
    const confirming = mode === "confirm";

    const code = el("code", { class: "cm-code language-python" });
    if (error) {
      code.textContent = error;
    } else {
      // highlight.js escapes its own output, so this is the one place
      // innerHTML is right -- textContent would show the markup.
      code.innerHTML = hljs.highlight(source || "", { language: "python" }).value;
    }
    const pre = el("pre", { class: "cm-pre", tabindex: "0" }, code);

    const actions = el("div", { class: "cm-actions" });
    const closeBtn = el("button", { type: "button", class: "btn" },
      confirming ? "Cancel" : "Close");
    actions.appendChild(closeBtn);
    let runBtn = null;
    if (confirming) {
      runBtn = el("button", { type: "button", class: "btn btn-primary", id: "cm-run" },
        "Run");
      actions.appendChild(runBtn);
    }

    const dialog = el("dialog", { class: "code-modal", id: "code-modal" },
      el("header", { class: "cm-head" },
        el("h2", {}, `${id}.py`),
        el("code", { class: "cm-path", title: path || "" }, path || ""),
      ),
      confirming
        ? el("p", { class: "cm-warning", id: "cm-warning" }, CODE_WARNING)
        : null,
      pre,
      actions,
    );

    let settled = false;
    const settle = (ran) => {
      if (settled) return;
      settled = true;
      if (dialog.parentNode) dialog.parentNode.removeChild(dialog);
      resolve(ran);
    };

    closeBtn.addEventListener("click", () => settle(false));
    if (runBtn) runBtn.addEventListener("click", () => settle(true));
    dialog.addEventListener("cancel", (e) => {
      // Esc must not count as approval.
      e.preventDefault();
      settle(false);
    });
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) settle(false);
    });

    document.body.appendChild(dialog);
    requestAnimationFrame(() => {
      dialog.showModal();
      // Focus the code, not Run: the dialog exists to be read, and
      // Enter should not start a run the user has not looked at.
      pre.focus();
    });
  });
}
