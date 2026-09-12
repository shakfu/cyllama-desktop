"""Sweep sampling parameters over a prompt set; write report.csv.

Runs every prompt at every temperature against the model the app already
has resident, so the whole grid costs one model load. Args:

    {"prompts": ["..."], "temperatures": [0.2, 0.7, 1.0],
     "max_tokens": 128, "model": "<path, optional>"}
"""

import csv
import time

from cyllama_desktop import app

args = app.args
prompts = args.get("prompts") or [
    "Name three prime numbers.",
    "Summarise what a vector database does, in one sentence.",
]
temperatures = args.get("temperatures") or [0.2, 0.7, 1.0]
max_tokens = int(args.get("max_tokens") or 128)
model = args.get("model") or app.default_model()

rows = []
total = len(prompts) * len(temperatures)
done = 0

for prompt in prompts:
    for temperature in temperatures:
        started = time.time()
        text = app.chat(
            prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed = time.time() - started
        rows.append({
            "prompt": prompt,
            "temperature": temperature,
            "seconds": round(elapsed, 2),
            "chars": len(text),
            "output": text.replace("\n", " ").strip(),
        })
        done += 1
        app.progress(done / total, f"{done}/{total} cells")

with open(app.artifact("report.csv"), "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

app.set_result({
    "model": model,
    "cells": len(rows),
    "seconds_total": round(sum(r["seconds"] for r in rows), 2),
})
