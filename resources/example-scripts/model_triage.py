"""Time every chat model against a fixed smoke set; write scoreboard.csv.

Run this after downloading a model to see how it compares on your
hardware. Args:

    {"prompts": ["..."], "max_tokens": 64, "models": ["<path>", ...]}

``models`` defaults to every chat-classified model the app can see.

This is the one example where reloads are expected and correct: the
sidecar keeps a single model resident, so each new ``model_path`` evicts
the previous one. Watch the Console for one ``[llm] loading`` line per
model, not per prompt.
"""

import csv
import time

from cyllama_desktop import app

args = app.args
prompts = args.get("prompts") or [
    "Explain what a hash table is, in two sentences.",
    "Name three prime numbers.",
]
max_tokens = int(args.get("max_tokens") or 64)

paths = args.get("models")
if not paths:
    paths = [m["path"] for m in app.models("chat")]
if not paths:
    raise SystemExit("no chat models found; import one in the Models pane")

rows = []
for i, path in enumerate(paths, 1):
    name = path.rsplit("/", 1)[-1]
    app.progress(i / len(paths), f"{i}/{len(paths)} {name}")
    seconds = 0.0
    tokens = 0
    error = ""
    for prompt in prompts:
        started = time.time()
        try:
            text = app.chat(prompt, model=path, max_tokens=max_tokens, temperature=0.0)
        except Exception as exc:          # a broken GGUF must not end the run
            error = f"{type(exc).__name__}: {exc}"
            break
        seconds += time.time() - started
        tokens += app.tokenize(text, model=path)
    rows.append({
        "model": name,
        "prompts": 0 if error else len(prompts),
        "seconds": 0.0 if error else round(seconds, 2),
        "tokens": 0 if error else tokens,
        "tokens_per_second": 0.0 if error or not seconds else round(tokens / seconds, 1),
        "error": error,
    })
    print(f"{rows[-1]['tokens_per_second']:>8.1f} tok/s  {name}  {error}".rstrip())

rows.sort(key=lambda r: r["tokens_per_second"], reverse=True)
with open(app.artifact("scoreboard.csv"), "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

app.set_result({
    "models": len(rows),
    "fastest": rows[0]["model"],
    "tokens_per_second": rows[0]["tokens_per_second"],
    "failed": [r["model"] for r in rows if r["error"]],
})
