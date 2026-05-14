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
        # Multimodal: ImageAnalyzer takes the underlying LlamaModel,
        # not the LLM wrapper. Real cyllama exposes it as ``.model``;
        # we expose a sentinel object so identity comparisons in tests
        # can verify the analyzer was built with our fake LLM's model.
        self.model = object()
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


class _FakeImageAnalyzer:
    """Stand-in for cyllama.llama.mtmd.ImageAnalyzer.

    Records constructor args so tests can assert the sidecar built the
    analyzer with the expected (mmproj, model) pair, and exposes
    ``answer_question`` returning a deterministic string the chat
    assertion can match.
    """
    instances: list["_FakeImageAnalyzer"] = []

    def __init__(self, mmproj_path, llama_model, **kwargs):  # noqa: ARG002
        self.mmproj_path = mmproj_path
        self.llama_model = llama_model
        self.calls: list[tuple[str, str]] = []
        type(self).instances.append(self)

    def answer_question(self, question, image):
        self.calls.append((question, image))
        return f"VISION ANSWER for {question!r} on {image!r}"


class _FakeBatchResponse:
    """Mirror cyllama.api.Response just enough for the batch job's
    ``.text`` / ``.stats`` access patterns."""
    def __init__(self, text: str):
        self.text = text
        self.stats = type("_FakeStats", (), {"__dict__": {"tokens": len(text)}})()


def _fake_batch_generate(prompts, model_path, batch_size=512, n_seq_max=8, **kwargs):  # noqa: ARG001
    # Echo each prompt as "ECHO: <prompt>" so tests can assert the
    # response text and ordering.
    return [_FakeBatchResponse(f"ECHO: {p}") for p in prompts]


class _FakeQuantizeParams:
    """Stand-in for cyllama.llama.llama_cpp.LlamaModelQuantizeParams.
    Cython class in real cyllama; plain dataclass-shape suffices here."""
    def __init__(self):
        self.ftype = 7
        self.nthread = 0
        self.allow_requantize = False
        self.quantize_output_tensor = True
        self.only_copy = False


def _fake_model_quantize(fname_inp, fname_out, params=None):  # noqa: ARG001
    # Pretend to write a quantized GGUF: copy a header-y blob to the
    # destination so subsequent stat() calls find a non-empty file.
    from pathlib import Path as _P
    _P(fname_out).write_bytes(b"GGUF\x00" + b"\x00" * 64)


class _FakeServerConfig:
    """Minimal stand-in for cyllama's ServerConfig dataclass."""
    def __init__(self, model_path, host="127.0.0.1", port=8080, **kwargs):
        self.model_path = model_path
        self.host = host
        self.port = port
        for k, v in kwargs.items(): setattr(self, k, v)


class _FakeServer:
    """Common base for the embedded + python server stubs.

    ``start_returns`` toggles the start() return value so tests can
    drive the failure branch without subclassing per-flavour.
    """
    instances: list["_FakeServer"] = []
    start_returns = True

    def __init__(self, config):
        self.config = config
        self.started = False
        self.stopped = False
        type(self).instances.append(self)

    def start(self):
        self.started = True
        return type(self).start_returns

    def stop(self):
        self.stopped = True


class _FakeEmbeddedServer(_FakeServer):
    instances: list["_FakeEmbeddedServer"] = []


class _FakePythonServer(_FakeServer):
    instances: list["_FakePythonServer"] = []


class _FakeAgentEventType:
    """Minimal stand-in for cyllama.agents.EventType.

    The sidecar reads ``ev.type.name`` so an enum-shaped object is enough.
    """
    def __init__(self, name):
        self.name = name


class _FakeAgentEvent:
    """Mirror cyllama.agents.types.AgentEvent (dataclass-like)."""
    def __init__(self, type, content="", metadata=None):
        self.type = type
        self.content = content
        self.metadata = metadata or {}


class _FakeAgentTool:
    """Match cyllama.agents.Tool's signature: name + description + func +
    parameters. The sidecar only inspects these fields, so a plain
    object satisfies it."""
    def __init__(self, name, description, func, parameters=None):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or {}


class _FakeReActAgent:
    """Stub the ReActAgent. ``stream`` yields a scripted trace so tests
    can assert on event ordering. Class-level ``_script`` is the
    knob tests poke to drive different behaviours."""
    instances: list["_FakeReActAgent"] = []
    # Default: one THOUGHT, one ANSWER. Tests override per-case.
    _script = [
        ("THOUGHT", "let me think"),
        ("ANSWER", "42"),
    ]

    def __init__(self, llm, tools=None, system_prompt=None,
                 max_iterations=10, verbose=False, **kwargs):  # noqa: ARG002
        self.llm = llm
        self.tools = list(tools or [])
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        type(self).instances.append(self)

    def stream(self, task):  # noqa: ARG002
        for kind, content in type(self)._script:
            yield _FakeAgentEvent(_FakeAgentEventType(kind), content)

    def run(self, task):
        """Mirror real cyllama.agents.ReActAgent.run: drain the stream,
        return an AgentResult-shape object. Tests for plan_and_execute
        rely on this synchronous form (planner.run / executor.run)."""
        steps = []
        answer = ""
        for ev in self.stream(task):
            steps.append(ev)
            etype = getattr(ev.type, "name", str(ev.type))
            if etype == "ANSWER":
                answer = ev.content
        return _FakeAgentResult(answer=answer, steps=steps,
                                iterations=len(steps), success=True)


class _FakeConstrainedAgent(_FakeReActAgent):
    """Stub for ``cyllama.agents.ConstrainedAgent``. Same surface as the
    ReAct fake; tests override ``_script`` to drive trace content. A
    distinct class so the sidecar's isinstance / kwargs handling can
    differ between the two endpoints if needed."""
    instances: list["_FakeConstrainedAgent"] = []


class _FakeContractAgent(_FakeReActAgent):
    """Stub for ``cyllama.agents.ContractAgent``. Stores the policy and
    rules so tests can assert pass-through, and -- when the default
    script's ANSWER violates a postcondition -- emits a synthetic
    CONTRACT_VIOLATION event before the ANSWER so the trace surfaces
    rule firing in the same shape real cyllama would."""
    instances: list["_FakeContractAgent"] = []

    def __init__(self, llm, tools=None, system_prompt=None,
                 policy=None, task_preconditions=None,
                 answer_postconditions=None, iteration_invariants=None,
                 max_iterations=10, verbose=False, **kwargs):  # noqa: ARG002
        super().__init__(llm, tools=tools, system_prompt=system_prompt,
                         max_iterations=max_iterations, verbose=verbose,
                         **kwargs)
        self.policy = policy
        self.task_preconditions = list(task_preconditions or [])
        self.answer_postconditions = list(answer_postconditions or [])
        self.iteration_invariants = list(iteration_invariants or [])

    def stream(self, task):
        # Check task preconditions; emit CONTRACT_VIOLATION for each
        # that fails. Note: under ENFORCE the real agent would
        # terminate; the stub just emits the event and continues so
        # tests can observe both the rule firing and downstream events.
        for rule in self.task_preconditions:
            try:
                ok = bool(rule(task))
            except Exception:
                ok = False
            if not ok:
                yield _FakeAgentEvent(
                    _FakeAgentEventType("CONTRACT_VIOLATION"),
                    "task precondition failed",
                )

        # Replay the base script (defaults to THOUGHT + ANSWER) and
        # check the ANSWER against each postcondition.
        last_answer = None
        for kind, content in type(self)._script:
            yield _FakeAgentEvent(_FakeAgentEventType(kind), content)
            if kind == "ANSWER":
                last_answer = content

        if last_answer is not None:
            for rule in self.answer_postconditions:
                try:
                    ok = bool(rule(last_answer))
                except Exception:
                    ok = False
                if not ok:
                    yield _FakeAgentEvent(
                        _FakeAgentEventType("CONTRACT_VIOLATION"),
                        "answer postcondition failed",
                    )


class _FakeContractPolicy:
    """Stub mirroring ``cyllama.agents.ContractPolicy``. Real cyllama
    exposes an IntEnum (IGNORE / OBSERVE / ENFORCE / QUICK_ENFORCE);
    tests only need name -> sentinel equality, not the int values."""
    IGNORE = "IGNORE"
    OBSERVE = "OBSERVE"
    ENFORCE = "ENFORCE"
    QUICK_ENFORCE = "QUICK_ENFORCE"


class _FakeAgentResult:
    """Mirror ``cyllama.agents.AgentResult`` (dataclass-shape)."""
    def __init__(self, answer="", steps=None, iterations=0,
                 success=True, error=None, metrics=None):
        self.answer = answer
        self.steps = list(steps or [])
        self.iterations = iterations
        self.success = success
        self.error = error
        self.metrics = metrics


class _FakeDryRunPlan:
    def __init__(self, entry, exits, levels, conditional_nodes, inputs_required):
        self.entry = entry
        self.exits = frozenset(exits)
        self.levels = tuple(tuple(lvl) for lvl in levels)
        self.conditional_nodes = frozenset(conditional_nodes)
        self.inputs_required = tuple(inputs_required)


class _FakeCompiledWorkflow:
    """Compiled-form fake. Supports astream() yielding scripted events
    and dry_run() returning a static plan derived from the builder
    inputs. Tests poke the workflow file's ``_script`` attribute to
    drive different event sequences.
    """

    def __init__(self, source):
        self._source = source

    def dry_run(self):
        return _FakeDryRunPlan(
            entry=self._source._entry,
            exits=self._source._exits or {self._source._entry},
            levels=[[self._source._entry]] + [[n] for n in self._source._other_nodes()],
            conditional_nodes=[],
            inputs_required=list(self._source._inputs_required),
        )

    def to_mermaid(self):
        return f"graph TD\n  {self._source._entry}([{self._source._entry}])"

    async def astream(self, initial_state=None):
        state = dict(initial_state or {})
        yield _FakeAgentEvent(_FakeAgentEventType("WORKFLOW_START"), "",
                              metadata={"entry": self._source._entry,
                                        "initial_state": dict(state)})
        # Replay scripted node events. Each entry is
        # (event_type, content, metadata_overrides).
        for entry in self._source._script:
            etype, content = entry[0], entry[1]
            md = entry[2] if len(entry) >= 3 else {}
            yield _FakeAgentEvent(_FakeAgentEventType(etype), content, metadata=dict(md))
        # Project the answer from a configured state key, falling back
        # to the entry node's name if it exists in state.
        answer_key = self._source._answer_key
        if answer_key and answer_key in state:
            answer = str(state[answer_key])
        else:
            answer = ""
        yield _FakeAgentEvent(_FakeAgentEventType("ANSWER"), answer,
                              metadata={"answer_key": answer_key})
        yield _FakeAgentEvent(_FakeAgentEventType("WORKFLOW_END"), "",
                              metadata={
                                  "state": state,
                                  "success": True,
                                  "error": None,
                                  "metrics": None,
                                  "nodes_run": list(self._source._all_nodes()),
                              })


class _FakeWorkflow:
    """Builder-form fake for cyllama.agents.Workflow.

    Minimal surface: add_node / add_edge / set_entry / set_exit + a
    compile() that returns a _FakeCompiledWorkflow. The astream output
    is driven by a class-level ``_default_script`` that test files can
    override per-instance.
    """

    # Default scripted node events used by every fake compiled workflow
    # whose source doesn't override. Just one NODE_START / NODE_END pair.
    _default_script = [
        ("NODE_START", "", {"node": "n1", "event_id": "ev-n1"}),
        ("NODE_END", "", {"node": "n1", "update": {}, "elapsed_ms": 0.1,
                          "event_id": "ev-n1"}),
    ]

    def __init__(self, *args, task_param="task", answer_key=None, **kwargs):  # noqa: ARG002
        self._entry = None
        self._exits = set()
        self._nodes = {}
        self._edges = []
        self._inputs_required = []
        self._script = list(type(self)._default_script)
        self._task_param = task_param
        self._answer_key = answer_key

    def add_node(self, name, fn=None, **kwargs):  # noqa: ARG002
        # Layer-B form: add_node(name, fn). Layer-C: add_node(fn).
        # Tests use Layer-B, which is the simpler path.
        node_name = name if isinstance(name, str) else getattr(name, "__name__", "node")
        self._nodes[node_name] = fn

    def add_edge(self, from_node, to_node):
        self._edges.append((from_node, to_node))

    def add_conditional_edge(self, from_node, router, edge_map=None):  # noqa: ARG002
        self._edges.append((from_node, "<conditional>"))

    def set_entry(self, name):
        self._entry = name

    def set_exit(self, name):
        self._exits.add(name)

    def declare_inputs(self, *names):
        """Test-only helper -- real Workflow infers these; the fake
        lets tests state them explicitly."""
        self._inputs_required = list(names)

    def set_script(self, script):
        """Test-only: replace the astream() event script."""
        self._script = list(script)

    def _other_nodes(self):
        return [n for n in self._nodes if n != self._entry]

    def _all_nodes(self):
        out = [self._entry] if self._entry else []
        out += self._other_nodes()
        return out

    def compile(self):
        if self._entry is None:
            raise ValueError("no entry node set")
        return _FakeCompiledWorkflow(self)


class _FakeSemanticMemoryRecord:
    def __init__(self, text, score=1.0, namespace="default", metadata=None):
        self.text = text
        self.score = score
        self.namespace = namespace
        self.metadata = metadata or {}


class _FakeSemanticMemory:
    """Mirror cyllama.agents.SemanticMemory just enough for tests.

    Records the ``rag`` shim + namespace on the instance; ``remember`` /
    ``retrieve`` are backed by an in-memory list keyed by namespace so
    a round-trip test can write and read a fragment without exercising
    a real vector store.
    """
    instances: list["_FakeSemanticMemory"] = []
    # Class-level storage so tests can clear between cases via the fixture.
    _store: dict[str, list[str]] = {}

    def __init__(self, rag, namespace_field="_memory_namespace", default_namespace="default"):
        self.rag = rag
        self.namespace_field = namespace_field
        self.default_namespace = default_namespace
        type(self).instances.append(self)

    def remember(self, text, *, namespace=None, metadata=None, split=False):  # noqa: ARG002
        ns = namespace or self.default_namespace
        bucket = type(self)._store.setdefault(ns, [])
        bucket.append(str(text))
        return [len(bucket) - 1]  # synthetic id

    def retrieve(self, query, *, namespace=None, top_k=5, threshold=None):  # noqa: ARG002
        ns = namespace or self.default_namespace
        bucket = type(self)._store.get(ns, [])
        # Trivial relevance: case-insensitive substring match on query,
        # ordered by recency, capped at top_k.
        q = str(query).lower()
        hits = [t for t in reversed(bucket) if q in t.lower()] or list(reversed(bucket))
        return [
            _FakeSemanticMemoryRecord(text=t, score=1.0 - i * 0.1, namespace=ns)
            for i, t in enumerate(hits[:top_k])
        ]


def _fake_stream_agent(
    kind,
    llm,
    task,
    *,
    tools=None,
    system_prompt=None,
    max_iterations=10,
    max_steps=10,
    planner_system_prompt=None,
    executor_system_prompt=None,
    stop_on_error=True,
    max_attempts=3,
    worker_system_prompt=None,
    critic_system_prompt=None,
    acceptance_marker="ACCEPT",
    critique_prefix=None,
    **agent_kwargs,
):
    """Stub for ``cyllama.agents.runner.stream_agent``.

    Mirrors the real runner's kind dispatch using the fake agent classes
    installed in this stub. Yields ``_FakeAgentEvent`` objects so the
    sidecar's per-event handling sees the same shape it would with the
    real runner.

    For ``"plan"`` / ``"reflect"`` the stub stamps ``metadata.source``
    onto each forwarded event (matching the real runner's ``_tag``
    behaviour) and emits a final ANSWER event with ``source = "final"``.
    """
    import re as _re

    tools = list(tools or [])

    def _tag(ev, source):
        md = dict(getattr(ev, "metadata", {}) or {})
        md["source"] = source
        return _FakeAgentEvent(ev.type, ev.content, metadata=md)

    if kind in {"react", "constrained", "contract"}:
        cls = {
            "react": _FakeReActAgent,
            "constrained": _FakeConstrainedAgent,
            "contract": _FakeContractAgent,
        }[kind]
        agent = cls(
            llm=llm, tools=tools, system_prompt=system_prompt,
            max_iterations=max_iterations, verbose=False, **agent_kwargs,
        )
        yield from agent.stream(task)
        return

    if kind == "plan":
        # Default planner prompt match keeps tests prompt-agnostic.
        planner = _FakeReActAgent(
            llm=llm, tools=[],
            system_prompt=planner_system_prompt,
            max_iterations=max_iterations, verbose=False,
        )
        plan_answer = None
        for ev in planner.stream(task):
            if getattr(ev.type, "name", "") == "ANSWER":
                plan_answer = ev.content
            yield _tag(ev, "planner")
        steps = []
        for line in (plan_answer or "").splitlines():
            s = _re.sub(r"^\s*(?:[-*+]|\d+[\.\)])\s+", "", line.strip())
            if s:
                steps.append(s)
        steps = steps[:max_steps]
        if not steps:
            yield _FakeAgentEvent(
                _FakeAgentEventType("ANSWER"),
                plan_answer or "",
                metadata={"source": "final", "plan": []},
            )
            return
        step_summaries = []
        for idx, step in enumerate(steps, start=1):
            executor = _FakeReActAgent(
                llm=llm, tools=tools,
                system_prompt=executor_system_prompt,
                max_iterations=max_iterations, verbose=False,
            )
            step_answer = None
            for ev in executor.stream(step):
                if getattr(ev.type, "name", "") == "ANSWER":
                    step_answer = ev.content
                yield _tag(ev, f"step-{idx}")
            step_summaries.append(f"{idx}. {step}\n   -> {step_answer or ''}")
            if step_answer is None and stop_on_error:
                break
        yield _FakeAgentEvent(
            _FakeAgentEventType("ANSWER"),
            "\n".join(step_summaries),
            metadata={"source": "final", "plan": steps},
        )
        return

    if kind == "reflect":
        marker = (acceptance_marker or "ACCEPT").upper()
        cprefix = critique_prefix or "Critique this draft:"
        current = task
        last_draft = ""
        accepted = False
        attempts = 0
        for n in range(1, max_attempts + 1):
            attempts = n
            worker = _FakeReActAgent(
                llm=llm, tools=tools,
                system_prompt=worker_system_prompt,
                max_iterations=max_iterations, verbose=False,
            )
            draft = None
            for ev in worker.stream(current):
                if getattr(ev.type, "name", "") == "ANSWER":
                    draft = ev.content
                yield _tag(ev, f"worker-{n}")
            if draft is None:
                yield _FakeAgentEvent(
                    _FakeAgentEventType("ERROR"),
                    "worker produced no answer",
                    metadata={"source": f"worker-{n}"},
                )
                break
            last_draft = draft
            critic = _FakeReActAgent(
                llm=llm, tools=[],
                system_prompt=critic_system_prompt,
                max_iterations=max_iterations, verbose=False,
            )
            critique = None
            for ev in critic.stream(f"{cprefix}\n\n{draft}"):
                if getattr(ev.type, "name", "") == "ANSWER":
                    critique = ev.content
                yield _tag(ev, f"critic-{n}")
            if critique and marker in critique.upper():
                accepted = True
                break
            current = (
                f"{task}\n\nYour previous attempt:\n{draft}\n\n"
                f"Critic feedback (please address):\n{critique or ''}"
            )
        yield _FakeAgentEvent(
            _FakeAgentEventType("ANSWER"),
            last_draft,
            metadata={"source": "final", "attempts": attempts, "accepted": accepted},
        )
        return

    raise ValueError(f"unknown agent kind: {kind!r}")


def _fake_plan_and_execute(planner, executor, task, **kwargs):  # noqa: ARG001
    """Stub for ``cyllama.agents.plan_and_execute``. Returns a list of
    per-step AgentResults built from the executor's scripted trace."""
    # Drive the planner to produce a fake plan, then run the executor
    # once per step. Tests override planner/executor _script as needed.
    plan_result = planner.run(task) if hasattr(planner, "run") else _FakeAgentResult(answer="step1\nstep2")
    if not getattr(plan_result, "success", True):
        return [plan_result]
    steps = [s.strip() for s in (plan_result.answer or "").splitlines() if s.strip()]
    results = []
    for step in steps:
        if hasattr(executor, "run"):
            results.append(executor.run(step))
        else:
            results.append(_FakeAgentResult(answer=f"did: {step}"))
    return results


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
    mod.batch_generate = _fake_batch_generate
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

    # Agents stub: cyllama.agents.{ReActAgent, Tool, AgentEvent, EventType}
    # plus the Phase-7+ additions probed by sidecar.py at module load.
    agents = types.ModuleType("cyllama.agents")
    agents.ReActAgent = _FakeReActAgent
    agents.Tool = _FakeAgentTool
    agents.AgentEvent = _FakeAgentEvent
    agents.EventType = _FakeAgentEventType
    agents.ConstrainedAgent = _FakeConstrainedAgent
    agents.ContractAgent = _FakeContractAgent
    agents.ContractPolicy = _FakeContractPolicy
    agents.AgentResult = _FakeAgentResult
    agents.plan_and_execute = _fake_plan_and_execute
    # Phase C: ReflectionLoop. The sidecar orchestrates worker+critic
    # itself (bypassing the wrapper for incremental streaming) and only
    # uses the symbol for feature detection, so a marker class suffices.
    agents.ReflectionLoop = type("_FakeReflectionLoop", (), {})
    # Phase D: Workflow / workflow_node / agent_node. The sidecar imports
    # workflow files at runtime and calls compile().dry_run() +
    # compile().astream(); the fakes implement just that surface.
    agents.Workflow = _FakeWorkflow
    agents.workflow_node = lambda *a, **kw: None  # marker for feature flag
    agents.agent_node = lambda *a, **kw: None     # marker for feature flag
    # Phase E: SemanticMemory. The sidecar constructs an instance with a
    # rag-shaped shim + namespace, then wraps ``remember`` / ``retrieve``
    # as Tools. The fake records the rag + namespace and provides minimal
    # remember/retrieve implementations backed by an in-memory list so
    # round-trip tests can verify wiring without a real vector store.
    agents.SemanticMemory = _FakeSemanticMemory

    # Phase 7+: cyllama.agents.tools stock @tool catalog. The sidecar
    # auto-injects current_time / calculator / word_count when available,
    # so the stub needs to provide them as Tool-shaped objects (name +
    # func) -- the agent classes only read these two fields.
    def _stock(name, fn, description=""):
        return _FakeAgentTool(name=name, description=description, func=fn)
    def _stock_current_time(timezone="UTC"):
        return {"timezone": timezone, "iso": "2026-05-13T00:00:00Z"}
    def _stock_calculator(expression: str) -> str:
        # Stub mirrors the real one: arithmetic only, raises on bad input.
        import ast as _ast
        import operator as _op
        OPS = {_ast.Add: _op.add, _ast.Sub: _op.sub, _ast.Mult: _op.mul,
               _ast.Div: _op.truediv, _ast.FloorDiv: _op.floordiv,
               _ast.Mod: _op.mod, _ast.Pow: _op.pow,
               _ast.USub: _op.neg, _ast.UAdd: _op.pos}
        def ev(node):
            if isinstance(node, _ast.Expression): return ev(node.body)
            if isinstance(node, _ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, _ast.BinOp) and type(node.op) in OPS:
                return OPS[type(node.op)](ev(node.left), ev(node.right))
            if isinstance(node, _ast.UnaryOp) and type(node.op) in OPS:
                return OPS[type(node.op)](ev(node.operand))
            raise ValueError(f"disallowed: {type(node).__name__}")
        return str(ev(_ast.parse(expression, mode="eval")))
    def _stock_word_count(text: str):
        return {
            "characters": len(text),
            "words": len(text.split()),
            "lines": 0 if not text else text.count("\n") + (0 if text.endswith("\n") else 1),
        }
    tools_mod = types.ModuleType("cyllama.agents.tools")
    tools_mod.current_time = _stock("current_time", _stock_current_time)
    tools_mod.calculator = _stock("calculator", _stock_calculator)
    tools_mod.word_count = _stock("word_count", _stock_word_count)
    def _stock_search_wikipedia(query: str, limit: int = 3):
        # Stub: returns a fixed result set sized by ``limit`` so tests
        # can assert the tool was wired without hitting the network.
        return [
            {"title": f"Result {i}", "snippet": f"about {query}",
             "url": f"https://en.wikipedia.org/wiki/Result_{i}"}
            for i in range(1, min(limit, 3) + 1)
        ]
    tools_mod.search_wikipedia = _stock("search_wikipedia", _stock_search_wikipedia)
    sys.modules["cyllama.agents.tools"] = tools_mod
    agents.tools = tools_mod

    # Phase 7+: cyllama.agents.runner.stream_agent dispatcher. The sidecar
    # probes ``cyllama.agents.runner`` so the runner needs to live as a
    # real module entry (not just an attribute on ``cyllama.agents``).
    runner = types.ModuleType("cyllama.agents.runner")
    runner.stream_agent = _fake_stream_agent
    sys.modules["cyllama.agents.runner"] = runner
    agents.runner = runner
    sys.modules["cyllama.agents"] = agents
    mod.agents = agents

    # cyllama.llama.llama_cpp stub for Phase 9 (quantize). Contains
    # the helper + params class the sidecar's _resolve_attr probes for.
    llama_cpp = types.ModuleType("cyllama.llama.llama_cpp")
    llama_cpp.model_quantize = _fake_model_quantize
    llama_cpp.LlamaModelQuantizeParams = _FakeQuantizeParams
    sys.modules["cyllama.llama.llama_cpp"] = llama_cpp

    # Multimodal: cyllama.llama.mtmd.ImageAnalyzer.
    mtmd = types.ModuleType("cyllama.llama.mtmd")
    mtmd.ImageAnalyzer = _FakeImageAnalyzer
    sys.modules["cyllama.llama.mtmd"] = mtmd

    # Server stubs: cyllama.llama.server.{embedded,python}.
    llama = types.ModuleType("cyllama.llama")
    llama.llama_cpp = llama_cpp
    llama.mtmd = mtmd
    server = types.ModuleType("cyllama.llama.server")
    embedded = types.ModuleType("cyllama.llama.server.embedded")
    embedded.EmbeddedServer = _FakeEmbeddedServer
    embedded.ServerConfig = _FakeServerConfig
    python_srv = types.ModuleType("cyllama.llama.server.python")
    python_srv.PythonServer = _FakePythonServer
    python_srv.ServerConfig = _FakeServerConfig
    sys.modules["cyllama.llama"] = llama
    sys.modules["cyllama.llama.server"] = server
    sys.modules["cyllama.llama.server.embedded"] = embedded
    sys.modules["cyllama.llama.server.python"] = python_srv
    llama.server = server
    server.embedded = embedded
    server.python = python_srv
    mod.llama = llama

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

    # cyllama.rag.loaders submodule. The sidecar probes this directly
    # for the document-extract path; needs its own module entry. Stub
    # mirrors the public surface the sidecar relies on: load_document,
    # PDFLoader, the registry + helpers, and the priority list.
    def _stub_loaders_load_document(path, **kwargs):  # noqa: ARG001
        from pathlib import Path as _P
        p = _P(path)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            # Pretend pypdf-stub extracted two pages worth of text.
            return [
                _FakeDocument(
                    text=f"[pdf body of {p.name}]",
                    metadata={"source": str(p), "filename": p.name,
                              "filetype": "pdf", "backend": "pypdf-stub"},
                ),
            ]
        if suffix not in {".txt", ".md", ".markdown", ".json", ".jsonl"}:
            raise ValueError(f"Unsupported file type: {suffix}")
        return [_FakeDocument(
            text=p.read_text(errors="ignore"),
            metadata={"source": str(p), "filename": p.name},
        )]
    class _StubPDFLoader:
        def __init__(self, backend="auto", per_page=False, require=(), **_):
            self.backend_name = "pypdf-stub"
        def load(self, path):
            return _stub_loaders_load_document(path)
    loaders = types.ModuleType("cyllama.rag.loaders")
    loaders.load_document = _stub_loaders_load_document
    loaders.PDFLoader = _StubPDFLoader
    loaders._PDF_BACKENDS = {"pypdf-stub": _StubPDFLoader}
    loaders._PDF_BACKEND_PRIORITY = ["pypdf-stub"]
    loaders.available_pdf_backends = lambda require=(): ["pypdf-stub"]
    loaders.pdf_backend_info = lambda name: {
        "name": name,
        "available": True,
        "capabilities": ["per_page"],
        "install_hint": "pip install pypdf",
    }
    sys.modules["cyllama.rag.loaders"] = loaders
    rag.loaders = loaders


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
    monkeypatch.setenv("CYLLAMA_SIDECAR_UPLOADS", str(tmp_path / "uploads"))

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
    _FakeReActAgent.instances.clear()
    _FakeReActAgent._script = [
        ("THOUGHT", "let me think"),
        ("ANSWER", "42"),
    ]
    _FakeImageAnalyzer.instances.clear()
    for cls in (_FakeServer, _FakeEmbeddedServer, _FakePythonServer):
        cls.instances.clear()
        # ``start_returns`` may have been shadowed on the subclass by a
        # test poking ``EmbeddedServer.start_returns = False``. Drop
        # the override so the next test sees the base True default
        # rather than the leaked False.
        if "start_returns" in cls.__dict__ and cls is not _FakeServer:
            delattr(cls, "start_returns")
    _FakeServer.start_returns = True


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
