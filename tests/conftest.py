"""Shared pytest fixtures.

The sidecar imports ``cyllama`` at module load. We stub it before importing
the sidecar so the test suite runs without a real cyllama install (CI
machines, or any dev box without a GGUF backend wheel). Tests that need
LLM behaviour drive it through the stub via ``monkeypatch``.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest


# --- cyllama stub -----------------------------------------------------------


class _FakeVocab:
    def tokenize(self, text, add_special=False, parse_special=False):  # noqa: ARG002
        # One token per whitespace-separated word; deterministic and good
        # enough to assert "more text => more tokens".
        return [0] * len(text.split())


class _FakeLLM:
    """Minimal stand-in for ``cyllama.LLM``.

    Tracks instances so tests can introspect cache eviction. ``chat`` and
    ``cancel`` are no-ops by default; tests override via monkeypatch when
    they need streaming or cancellation behaviour.
    """

    instances: list["_FakeLLM"] = []

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self.vocab = _FakeVocab()
        self.closed = False
        self.cancelled = False
        _FakeLLM.instances.append(self)

    def chat(self, messages, stream=False, config=None):  # noqa: ARG002
        if not stream:
            return "ok"

        def gen():
            yield "hello"
            yield " "
            yield "world"

        return gen()

    def cancel(self) -> None:
        self.cancelled = True

    def close(self) -> None:
        self.closed = True


class _FakeGenerationConfig:
    """Stub for ``cyllama.GenerationConfig``.

    Mirrors the real cyllama 0.2.15 signature -- ``temperature``,
    ``top_p``, ``top_k``, ``min_p``, ``repeat_penalty``, ``max_tokens``,
    ``seed``, ``stop_sequences`` -- so ``inspect.signature`` returns the
    same set the renderer is supposed to filter against. Tests asserting
    "unsupported fields are dropped" can monkeypatch ``_GC_ACCEPTED``
    in the sidecar module to simulate a stricter or laxer cyllama.
    """

    def __init__(
        self,
        temperature: float = 0.8,
        top_p: float = 0.95,
        top_k: int = 40,
        min_p: float = 0.05,
        repeat_penalty: float = 1.0,
        max_tokens: int = 512,
        seed: int = 0,
        stop_sequences=None,
    ) -> None:
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.min_p = min_p
        self.repeat_penalty = repeat_penalty
        self.max_tokens = max_tokens
        self.seed = seed
        self.stop_sequences = list(stop_sequences or [])

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def __getitem__(self, key: str):
        return getattr(self, key)


class _FakeBackend:
    cuda = False
    metal = True
    rocm = False
    vulkan = False
    sycl = False
    opencl = False


class _FakeGGUFContext:
    """Stub for ``cyllama.GGUFContext``.

    Tests override ``_metadata`` per fixture to drive different return
    shapes. The class shape (``from_file`` + ``get_all_metadata``) tracks
    what the sidecar's introspection probes.
    """

    _metadata: dict = {"general.architecture": "llama", "general.name": "fake"}
    last_path: str = ""

    def __init__(self, path: str) -> None:
        type(self).last_path = path

    @classmethod
    def from_file(cls, path: str) -> "_FakeGGUFContext":
        return cls(path)

    def get_all_metadata(self) -> dict:
        return dict(type(self)._metadata)


def _install_cyllama_stub() -> None:
    if "cyllama" in sys.modules:
        return
    mod = types.ModuleType("cyllama")
    mod.__version__ = "0.0.0-test"
    mod.LLM = _FakeLLM
    mod.GenerationConfig = _FakeGenerationConfig
    mod._backend = _FakeBackend
    mod.GGUFContext = _FakeGGUFContext
    sys.modules["cyllama"] = mod


_install_cyllama_stub()


# --- sidecar import (must happen after the stub is in place) ---------------


@pytest.fixture()
def sidecar_app(tmp_path, monkeypatch):
    """Return the FastAPI app with a fresh module state per test.

    Re-imports ``sidecar`` so module-level globals (LLM cache, jobs registry)
    don't leak between tests. The artifact dir is redirected into ``tmp_path``.
    """
    monkeypatch.setenv("CYLLAMA_SIDECAR_PORT", "0")
    monkeypatch.setenv("CYLLAMA_SIDECAR_TOKEN", "test-token")
    monkeypatch.setenv("CYLLAMA_SIDECAR_PARENT_PID", "0")
    monkeypatch.setenv("CYLLAMA_SIDECAR_ARTIFACTS", str(tmp_path / "artifacts"))
    monkeypatch.setenv("CYLLAMA_SIDECAR_MODELS", str(tmp_path / "models"))

    sidecar_path = Path(__file__).resolve().parent.parent / "python-sidecar"
    sys.path.insert(0, str(sidecar_path))
    sys.modules.pop("sidecar", None)
    import sidecar  # noqa: F401

    # Neutralize the real HF caches so tests don't enumerate the dev
    # machine's models. Point at a tmp subdir that doesn't exist.
    sidecar._HF_CACHE_DIRS = (tmp_path / "_no_hf_cache",)

    yield sidecar

    sys.modules.pop("sidecar", None)
    sys.path.remove(str(sidecar_path))
    _FakeLLM.instances.clear()


@pytest.fixture()
def client(sidecar_app):
    from fastapi.testclient import TestClient
    return TestClient(sidecar_app.app)


@pytest.fixture()
def auth():
    return {"authorization": "Bearer test-token"}


@pytest.fixture()
def fake_model(tmp_path):
    """A path that exists on disk so ``_get_llm`` accepts it."""
    p = tmp_path / "fake.gguf"
    p.write_bytes(b"GGUF\x00")
    return str(p)
