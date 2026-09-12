"""Find questions your RAG collection cannot answer; write gaps.csv.

Retrieval only -- no model is loaded, so this is cheap and safe to
re-run after changing chunk size, re-ingesting, or swapping the
embedding model. Args:

    {"collection": "<id>", "questions": ["..."], "top_k": 3,
     "min_score": 0.3}

``collection`` defaults to the first one. A question whose best chunk
scores below ``min_score`` is reported as a gap: either the corpus does
not cover it, or the chunking buried it.
"""

import csv

from cyllama_desktop import app

args = app.args
collections = app.rag_collections()
if not collections:
    raise SystemExit("no RAG collections; create one in the Docs pane first")

coll_id = args.get("collection") or collections[0]["id"]
known = {c["id"] for c in collections}
if coll_id not in known:
    raise SystemExit(f"unknown collection {coll_id!r}; have {sorted(known)}")

questions = args.get("questions")
if not questions:
    raise SystemExit('pass questions, e.g. {"questions": ["how do I ...?"]}')
top_k = int(args.get("top_k") or 3)
min_score = float(args.get("min_score") or 0.3)

rows = []
for i, question in enumerate(questions, 1):
    app.progress(i / len(questions), f"{i}/{len(questions)}")
    sources = app.rag_retrieve(coll_id, question, top_k=top_k)
    best = sources[0] if sources else None
    score = float(best["score"]) if best else 0.0
    rows.append({
        "question": question,
        "hits": len(sources),
        "best_score": round(score, 3),
        "gap": "yes" if score < min_score else "",
        "top_chunk": (best["text"][:160].replace("\n", " ") if best else ""),
    })
    print(f"{'GAP ' if score < min_score else '    '}{score:.3f}  {question}")

with open(app.artifact("gaps.csv"), "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

gaps = [r for r in rows if r["gap"]]
app.set_result({
    "collection": coll_id,
    "questions": len(rows),
    "gaps": len(gaps),
    "worst": min((r["best_score"] for r in rows), default=0.0),
})
