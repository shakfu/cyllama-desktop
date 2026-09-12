"""Check a model's answers against expected substrings; write results.json.

Re-run this after bumping a model or editing a system prompt to see what
changed. Args:

    {"cases": [{"prompt": "...", "expect": "..."}],
     "system": "<optional system prompt>", "model": "<path, optional>"}

A case passes when every string in ``expect`` appears in the answer,
case-insensitively. ``expect`` may be a string or a list of strings.
"""

from cyllama_desktop import app

args = app.args
cases = args.get("cases") or [
    {"prompt": "What is 17 + 25?", "expect": "42"},
    {"prompt": "Which planet is closest to the sun?", "expect": "mercury"},
]
system = args.get("system") or ""
model = args.get("model") or app.default_model()

results = []
for i, case in enumerate(cases, 1):
    expect = case.get("expect") or []
    if isinstance(expect, str):
        expect = [expect]
    answer = app.chat(case["prompt"], model=model, system=system, temperature=0.0)
    lowered = answer.lower()
    missing = [e for e in expect if e.lower() not in lowered]
    results.append({
        "prompt": case["prompt"],
        "answer": answer.strip(),
        "expect": expect,
        "missing": missing,
        "passed": not missing,
    })
    app.progress(i / len(cases), f"case {i}/{len(cases)}")
    print(f"{'PASS' if not missing else 'FAIL'}  {case['prompt']}")

passed = sum(1 for r in results if r["passed"])
app.set_result({
    "model": model,
    "passed": passed,
    "failed": len(results) - passed,
    "cases": results,
})
print(f"{passed}/{len(results)} passed")
