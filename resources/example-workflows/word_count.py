"""Word count -- a linear pipeline that tokenises an input string,
counts the tokens, and emits a summary line.

Demonstrates the Layer C decorator form: each @flow.node decorator
registers a node whose parameter names are inferred dependencies on
other nodes (or required workflow inputs when no matching node exists).

Run from the Workflows pane: supply a 'text' value and click Run.
"""

from __future__ import annotations

import re

from cyllama.agents.workflow import Workflow


flow = Workflow()


@flow.node
def tokens(text: str) -> list[str]:
    """Split the input on whitespace + punctuation, lowercased."""
    return [t for t in re.split(r"\W+", (text or "").lower()) if t]


@flow.node
def count(tokens: list[str]) -> int:
    """Count tokens. Implicit dependency on ``tokens`` via the param name."""
    return len(tokens)


@flow.node
def summary(text: str, count: int) -> str:
    """Human-readable summary line. Depends on both ``text`` (the
    workflow input) and ``count`` (a sibling node's output)."""
    snippet = (text or "").strip()
    if len(snippet) > 60:
        snippet = snippet[:57] + "..."
    return f"{count} tokens in {snippet!r}"


flow.set_entry("tokens")
flow.set_exit("summary")
