"""Run one question set under two system prompts; write ab.csv.

Both variants go through the model the app already has resident, so the
comparison costs one load. Args:

    {"a": "<system prompt A>", "b": "<system prompt B>",
     "prompts": ["..."], "max_tokens": 128, "model": "<path, optional>"}

Use it when editing a system prompt and you want to see what actually
changed rather than trusting a single spot check.
"""

import csv

from cyllama_desktop import app

args = app.args
system_a = args.get("a") or "You are a helpful assistant."
system_b = args.get("b") or "You are terse. Answer in one short sentence."
prompts = args.get("prompts") or [
    "Why is the sky blue?",
    "What is a vector database for?",
]
max_tokens = int(args.get("max_tokens") or 128)
model = args.get("model") or app.default_model()

rows = []
for i, prompt in enumerate(prompts, 1):
    app.progress(i / len(prompts), f"{i}/{len(prompts)}")
    answers = {}
    for key, system in (("a", system_a), ("b", system_b)):
        answers[key] = app.chat(
            prompt, model=model, system=system,
            max_tokens=max_tokens, temperature=0.0,
        ).strip()
    rows.append({
        "prompt": prompt,
        "a_chars": len(answers["a"]),
        "b_chars": len(answers["b"]),
        "identical": "yes" if answers["a"] == answers["b"] else "",
        "a": answers["a"].replace("\n", " "),
        "b": answers["b"].replace("\n", " "),
    })
    print(f"{rows[-1]['a_chars']:>5} vs {rows[-1]['b_chars']:<5}  {prompt}")

with open(app.artifact("ab.csv"), "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

app.set_result({
    "model": model,
    "prompts": len(rows),
    "identical": sum(1 for r in rows if r["identical"]),
    "mean_chars_a": round(sum(r["a_chars"] for r in rows) / len(rows), 1),
    "mean_chars_b": round(sum(r["b_chars"] for r in rows) / len(rows), 1),
})
