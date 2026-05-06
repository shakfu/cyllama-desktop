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

    def __init__(self, model_path: str, config=None, **kwargs) -> None:
        # cyllama 0.2.15's LLM accepts (model_path, config, verbose,
        # cache_size, cache_ttl, **kwargs). Mirror that loosely so the
        # sidecar's new "LLM(path, config=...)" call path doesn't blow
        # up the test stub.
        self.model_path = model_path
        self.config = config
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


class _FakeDocument:
    def __init__(self, text: str, metadata: dict | None = None, id: str | None = None) -> None:
        self.text = text
        self.metadata = metadata or {}
        self.id = id


class _FakeChunk:
    def __init__(
        self,
        text: str,
        metadata: dict | None = None,
        source_id: str | None = None,
        chunk_index: int = 0,
    ) -> None:
        self.text = text
        self.metadata = metadata or {}
        self.source_id = source_id
        self.chunk_index = chunk_index


class _FakeEmbedder:
    """Stub for ``cyllama.rag.Embedder``.

    ``embed_batch`` returns deterministic 8-dim vectors so RAG ingest
    tests can assert "things got stored" without needing a real GGUF
    embedding model.
    """

    instances: list["_FakeEmbedder"] = []

    def __init__(self, model_path: str, **kwargs) -> None:
        self.model_path = model_path
        self.dimension = 8
        self.closed = False
        type(self).instances.append(self)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] * self.dimension for t in texts]

    def close(self) -> None:
        self.closed = True


class _FakeSqliteVectorStore:
    """Stub mimicking ``cyllama.rag.SqliteVectorStore``.

    Real cyllama shares state through the sqlite file on disk; instances
    opening the same ``db_path`` see the same rows. Mirror that with a
    class-level ``_rows_by_path`` map so an ``add()`` from the ingest
    job is visible to a later ``search()`` from /rag/retrieve.
    """

    instances: list["_FakeSqliteVectorStore"] = []
    _rows_by_path: dict = {}

    def __init__(self, dimension: int, db_path: str = ":memory:", **kwargs) -> None:
        self.dimension = dimension
        self.db_path = db_path
        # ``setdefault`` so a later opener attaches to the same list ingest
        # populated. ``:memory:`` collapses everything to one shared list,
        # which is fine for tests that don't drive multiple in-memory stores.
        self.rows: list[tuple] = type(self)._rows_by_path.setdefault(db_path, [])
        type(self).instances.append(self)

    def add(self, embeddings, texts, metadata=None, source_hash=None, source_label=None):  # noqa: ARG002
        ids = []
        for i, (e, t) in enumerate(zip(embeddings, texts)):
            md = metadata[i] if metadata else {}
            self.rows.append((e, t, md))
            ids.append(len(self.rows))
        return ids

    def search(self, query_embedding, k: int = 5, threshold=None):  # noqa: ARG002
        out = []
        for i, (_emb, text, md) in enumerate(self.rows[:k]):
            out.append(_FakeSearchResult(
                id=str(i),
                text=text,
                score=1.0 / (1 + i),
                metadata=md or {},
            ))
        return out

    def close(self) -> None:
        pass


class _FakeTextSplitter:
    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50, **kwargs) -> None:
        self.chunk_size = max(1, chunk_size)
        self.chunk_overlap = chunk_overlap

    def split_documents(self, documents):
        out = []
        for d in documents:
            words = (d.text or "").split()
            if not words:
                continue
            for i in range(0, len(words), self.chunk_size):
                out.append(_FakeChunk(
                    text=" ".join(words[i:i + self.chunk_size]),
                    metadata=dict(getattr(d, "metadata", {}) or {}),
                    source_id=getattr(d, "id", None),
                    chunk_index=i,
                ))
        return out


class _FakeSearchResult:
    def __init__(self, id: str, text: str, score: float, metadata: dict | None = None) -> None:
        self.id = id
        self.text = text
        self.score = score
        self.metadata = metadata or {}


class _FakeRAGConfig:
    """Mirrors a useful subset of ``cyllama.rag.RAGConfig``."""

    def __init__(
        self,
        top_k: int = 5,
        similarity_threshold=None,
        max_tokens: int = 512,
        temperature: float = 0.8,
        system_prompt=None,
        **kwargs,
    ) -> None:
        self.top_k = top_k
        self.similarity_threshold = similarity_threshold
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.system_prompt = system_prompt
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeRAG:
    """Stub for ``cyllama.rag.RAG``.

    Tests drive ``stream`` and ``retrieve`` outputs by monkeypatching
    class-level ``_chunks`` / ``_sources``. Construction tracks the
    embedding/generation model paths so cache-eviction tests can
    introspect them.
    """

    instances: list["_FakeRAG"] = []
    _chunks: list[str] = ["hello", " ", "world"]
    _sources: list = []
    closed_count: int = 0

    def __init__(self, embedding_model: str, generation_model: str, db_path: str = ":memory:", **kwargs) -> None:
        self.embedding_model = embedding_model
        self.generation_model = generation_model
        self.db_path = db_path
        self.closed = False
        self.llm = type("_FakeRAGLLM", (), {"cancel": lambda self: None})()
        type(self).instances.append(self)

    def retrieve(self, question: str, config=None):  # noqa: ARG002
        return list(type(self)._sources)

    def stream(self, question: str, config=None):  # noqa: ARG002
        for c in type(self)._chunks:
            yield c

    def query(self, question: str, config=None):  # noqa: ARG002
        return type("_FakeRAGResp", (), {
            "text": "".join(type(self)._chunks),
            "sources": list(type(self)._sources),
            "stats": None,
            "query": question,
        })()

    def close(self) -> None:
        self.closed = True
        type(self).closed_count += 1


def _fake_load_document(path, **kwargs):  # noqa: ARG001
    p = Path(path)
    return [_FakeDocument(text=p.read_text(errors="ignore"), metadata={"source": str(p)})]


def _fake_load_directory(path, glob: str = "**/*", **kwargs):  # noqa: ARG001
    out = []
    for f in Path(path).glob(glob):
        if f.is_file():
            out.extend(_fake_load_document(str(f)))
    return out


class _FakeWhisperFullParams:
    """Mirror the subset of WhisperFullParams the sidecar sets."""
    def __init__(self):
        self.print_progress = True
        self.print_realtime = True
        self.print_timestamps = True
        self.print_special = True
        self.translate = False
        self.no_timestamps = False
        self.language = ""
        self.n_threads = 0


class _FakeWhisperContextParams:
    pass


class _FakeWhisperContext:
    """Tiny stand-in for cyllama.whisper.whisper_cpp.WhisperContext.

    ``full`` records the call and pretends three short segments were
    decoded; the segment accessors then read those out one at a time.
    Tests assert on the per-segment SSE events the sidecar emits.
    """
    instances: list["_FakeWhisperContext"] = []
    _segments = [
        (0, 100, " hello"),         # 0.0s - 1.0s, leading space mimics whisper
        (100, 200, " world"),       # 1.0s - 2.0s
        (200, 320, " again"),       # 2.0s - 3.2s
    ]

    def __init__(self, model_path: str, ctx_params=None) -> None:
        self.model_path = model_path
        self.ctx_params = ctx_params
        self.full_called_with = None
        self.closed = False
        type(self).instances.append(self)

    def full(self, samples, params):
        self.full_called_with = (samples, params)

    def full_n_segments(self) -> int:
        return len(self._segments)

    def full_get_segment_t0(self, i): return self._segments[i][0]
    def full_get_segment_t1(self, i): return self._segments[i][1]
    def full_get_segment_text(self, i): return self._segments[i][2]
    def full_lang_id(self): return 0
    def lang_str(self, _id): return "en"

    def close(self): self.closed = True


class _FakeSDImage:
    """Minimal stand-in for cyllama.sd.SDImage.

    Records save_png calls so tests can assert the file lands in the
    job's artifact directory.
    """
    saved_paths: list[str] = []

    def __init__(self, width=512, height=512):
        self.width = width
        self.height = height
        self.channels = 3

    def is_valid(self): return True

    def save_png(self, path: str):
        # Write a tiny PNG-ish blob so /jobs/<id>/artifact/<name> can
        # serve a non-empty file when tests fetch it.
        from pathlib import Path as _P
        _P(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        type(self).saved_paths.append(path)


def _fake_text_to_image(model_path, prompt, negative_prompt="", width=512, height=512,
                        seed=-1, sample_steps=20, cfg_scale=7.0, **kwargs):  # noqa: ARG001
    return _FakeSDImage(width=width, height=height)


def _fake_load_wav_file(path):
    # Return a sentinel "samples" + 16 kHz so the resampler short-circuits.
    return ([0.0, 0.0, 0.0], 16000)


def _fake_resample_audio(samples, src_sr, dst_sr=16000):  # noqa: ARG001
    return samples


def _fake_json_schema_to_grammar(schema, force_gbnf: bool = False):  # noqa: ARG001
    # Just enough to assert the wire shape end-to-end. Doesn't pretend
    # to produce a real GBNF for arbitrary schemas.
    if not isinstance(schema, dict):
        raise ValueError("schema must be a dict")
    return f'root ::= "stub" # keys={sorted(schema.keys())}\n'


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

    # cyllama.utils.json_schema_to_grammar -- the sidecar resolves this
    # via importlib.import_module so it has to exist as a real module
    # entry, not just an attribute on the package.
    utils = types.ModuleType("cyllama.utils")
    js2g = types.ModuleType("cyllama.utils.json_schema_to_grammar")
    js2g.json_schema_to_grammar = _fake_json_schema_to_grammar
    utils.json_schema_to_grammar = _fake_json_schema_to_grammar
    sys.modules["cyllama.utils"] = utils
    sys.modules["cyllama.utils.json_schema_to_grammar"] = js2g
    mod.utils = utils

    # Whisper stub: cyllama.whisper.{whisper_cpp,cli}. Same pattern --
    # the sidecar's _resolve_attr does importlib.import_module so each
    # path needs a real entry in sys.modules.
    whisper = types.ModuleType("cyllama.whisper")
    whisper_cpp = types.ModuleType("cyllama.whisper.whisper_cpp")
    whisper_cpp.WhisperContext = _FakeWhisperContext
    whisper_cpp.WhisperContextParams = _FakeWhisperContextParams
    whisper_cpp.WhisperFullParams = _FakeWhisperFullParams
    whisper_cli = types.ModuleType("cyllama.whisper.cli")
    whisper_cli.load_wav_file = _fake_load_wav_file
    whisper_cli.resample_audio = _fake_resample_audio
    sys.modules["cyllama.whisper"] = whisper
    sys.modules["cyllama.whisper.whisper_cpp"] = whisper_cpp
    sys.modules["cyllama.whisper.cli"] = whisper_cli
    whisper.whisper_cpp = whisper_cpp
    whisper.cli = whisper_cli
    mod.whisper = whisper

    # Stable-diffusion stub: cyllama.sd.{text_to_image, SDImage}.
    sd = types.ModuleType("cyllama.sd")
    sd.text_to_image = _fake_text_to_image
    sd.SDImage = _FakeSDImage
    sys.modules["cyllama.sd"] = sd
    mod.sd = sd

    rag = types.ModuleType("cyllama.rag")
    rag.Document = _FakeDocument
    rag.Chunk = _FakeChunk
    rag.Embedder = _FakeEmbedder
    rag.SqliteVectorStore = _FakeSqliteVectorStore
    rag.TextSplitter = _FakeTextSplitter
    rag.RAG = _FakeRAG
    rag.RAGConfig = _FakeRAGConfig
    rag.SearchResult = _FakeSearchResult
    rag.load_document = _fake_load_document
    rag.load_directory = _fake_load_directory
    sys.modules["cyllama.rag"] = rag
    mod.rag = rag


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
    monkeypatch.setenv("CYLLAMA_SIDECAR_RAG", str(tmp_path / "rag"))

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
    _FakeEmbedder.instances.clear()
    _FakeSqliteVectorStore.instances.clear()
    _FakeSqliteVectorStore._rows_by_path.clear()
    _FakeRAG.instances.clear()
    _FakeRAG._chunks = ["hello", " ", "world"]
    _FakeRAG._sources = []
    _FakeRAG.closed_count = 0
    _FakeWhisperContext.instances.clear()
    _FakeSDImage.saved_paths.clear()


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


@pytest.fixture()
def fake_wav(tmp_path):
    """A path with .wav suffix for the transcribe job's suffix check."""
    p = tmp_path / "audio.wav"
    p.write_bytes(b"RIFF\x00")
    return str(p)
