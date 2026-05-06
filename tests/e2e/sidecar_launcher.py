"""Sidecar entry-point used by the Playwright e2e suite.

Imports the conftest cyllama stub *before* the real sidecar so the
FastAPI app the renderer talks to has deterministic behaviour without
any real cyllama backend or GGUF files. Equivalent to what
``tests/conftest.py``'s ``sidecar_app`` fixture does, but as a
standalone process so Electron's spawn() can launch it.

Required env vars (set by main.js' ``startSidecar``):

  CYLLAMA_SIDECAR_PORT, CYLLAMA_SIDECAR_TOKEN,
  CYLLAMA_SIDECAR_PARENT_PID, CYLLAMA_SIDECAR_ARTIFACTS,
  CYLLAMA_SIDECAR_MODELS, CYLLAMA_SIDECAR_RAG.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

# Repo root: tests/e2e/sidecar_launcher.py -> two levels up.
ROOT = Path(__file__).resolve().parent.parent.parent

# Install the cyllama stub by importing ``tests/conftest.py``. The stub
# install runs at module load via ``_install_cyllama_stub()``. The pytest
# fixtures inside conftest are decorators -- harmless when imported
# without pytest running.
sys.path.insert(0, str(ROOT / "tests"))
import conftest  # noqa: F401,E402  -- side-effect: stub install

# Now load the real sidecar module. ``sidecar.py`` reads its env at
# import, so the env vars must be set by the parent before launch.
sys.path.insert(0, str(ROOT / "python-sidecar"))
import sidecar  # noqa: E402

# Disable the HF cache scan so the launcher doesn't enumerate the dev
# machine's models (matches the conftest fixture's neutralization).
sidecar._HF_CACHE_DIRS = ()

if __name__ == "__main__":
    uvicorn.run(
        sidecar.app,
        host="127.0.0.1",
        port=int(os.environ["CYLLAMA_SIDECAR_PORT"]),
        log_level="warning",
    )
