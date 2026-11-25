# APRIL Rebuild — Phased Implementation Plan

**Status:** implemented (P0–P10) · **Date:** 2026-11-25
**Supersedes:** `/Users/aditya/.opencode/plan` (v1, 2026-10-30) and the "process-boundary" integration model it locked.

> **Execution status (2026-11-25):** P0–P9 landed in `april/`; P10 deleted `core/` and
> `tools/` after a module-by-module parity audit (a temp backup was taken first).
> `python3 -m april` is the offline self-check; `pytest tests` covers the API and TUI.
> Intentional retirements: ADE deploy/boot web screens, browser automation, the web
> harness runner, the IPC log tab, the curses TUI, the c1–c8 component editors (collapsed
> into the workflow surface), and the ZCode substrate/subprocess channels.

> The v1 plan said: *ZCode is an untouched process-boundary execution target; zero source changes.*
> That is reversed. **ZCode and Harness2 are reference only, and both are ported, never called.**

---

## 1. Locked decisions

### From the user

- The runtime is the **one composer / controller**; modules own the mechanism.
- Modules are reusable; `learning/` is instantiable against multiple targets (memory, live session, hardware).
- **Port, never call.** ZCode's tool layer is ported into the APRIL runtime independently.
- Keep Harness2's full loop, but modular and reusable.
- `llm/` is a dumb backend: request in, response out.
- Memory: simplest version now; smarter retrieval later.
- Body: basic; the first limb is telemetry only.
- Skills: user-scoped vs module-scoped.

### Architecture decisions (2026-11-24)

| # | Decision | Value |
|---|---|---|
| A | ZCode port scope | **Extension layer only** — MCP, skills, hooks, plugins, subagents, plus their schemas and trust semantics. **Not** the turn machine, model-provider layer, desktop/web UI, or the bundled marketplace plugins (those are reference material; only the loader is ported). |
| B | `llm/` shape | **Pure pipe** — `complete()` / `stream()` only. No agent loop, no tools, no persona. The learning engine builds its own small file-editing loop on top. |
| C | Skills scope schema | `scope: user` or `scope: module:<name>` in `SKILL.md` frontmatter. User-scoped loads into the global library; module-scoped loads only while that module is active. |
| D | Migration | **Strangler** — build `april/` beside `core/`, adopt shared leaves early, switch the entry point when `april/runtime` can answer a turn, delete `core/` last. |

### The two fixes that make the architecture score 4.3

1. **Orchestration, not implementation.** The runtime owns the turn sequence and the wiring. It never parses frontmatter, runs SQL, or speaks HTTP. Enforced by an architecture-guard test.
2. **The `Learnable` seam.** `learning/` is generic over `read/edit/validate/evaluate`; the runtime injects one of `WorkflowTarget`, `MemoryTarget`, `DeviceTarget`.

---

## 2. Target module map

| Module | Interface (the small surface) | Seam → adapters | Depth behind the seam | Depends on |
|---|---|---|---|---|
| `runtime/` | `respond(input, session)`, `stream(...)` | — (it is the composer) | turn sequence, message assembly, policy, tool loop | all interfaces |
| `llm/` | `complete(req)`, `stream(req)` | **`Model`**: `openai_compatible`, `fake` | retries, streaming assembly, cost, provider quirks | — |
| `learning/` | `Engine(target, model, library).run(trace)` | **`Learnable`**: `WorkflowTarget`, `MemoryTarget`, `DeviceTarget` | propose/aggregate/compose, blind selection, library, judge, ledger | `llm/` |
| `persona/` | `compile(profile) -> Persona` | **`ProfileSource`**: `file`, `deployed` | OCEAN math, facets, PSY, compiled text, guards | — |
| `memory/` | `recall(q, k)`, `remember(turn)` | **`MemoryStore`**: `sqlite`, `vector` (later) | sessions, extraction, ranking, retention | `llm/` |
| `tools/` | `schemas()`, `dispatch(name, args)`, `fire_hook(evt)` | **`ToolSource`**: `mcp`, `skills`, `builtin` | JSON-RPC, frontmatter, trust gates, manifests | `events/` |
| `events/` | `emit(event)`, `subscribe()` | **`Sink`**: `ring-buffer`, `file`, `sse` | serialization, replay, logging | — |
| `body/` | `devices()`, `read(device)` | **`Device`**: `esp32_telemetry`, `fake` | HTTP, parsing, risk level | — |
| `api/` | HTTP routes | — | request parsing, SSE | `runtime/` |
| `cli/` | TUI | — | terminal | `runtime/` |
| `config/` | `Settings` | env/YAML | — | — |

**Dependency rule:** `runtime/` imports interfaces only. No module imports another module's concrete adapter. `app.py` (the composition root) is the only file that constructs concrete adapters.

---

## 3. The five load-bearing interfaces

```python
# llm/ — the dumb pipe. No tools, no loop, no persona.
class Model(Protocol):
    def complete(self, req: ChatRequest) -> ChatReply: ...
    def stream(self, req: ChatRequest) -> Iterator[Delta]: ...

# learning/ — the reusable engine.
class Learnable(Protocol):
    def read(self) -> Snapshot: ...
    def edit(self, proposal: Proposal) -> Snapshot: ...
    def validate(self, snapshot: Snapshot) -> list[str]: ...
    def evaluate(self, snapshot: Snapshot, evidence: Evidence) -> Score: ...

class Engine:
    def __init__(self, target: Learnable, model: Model, library: LessonLibrary): ...
    def run(self, trace: Trace) -> Lesson | None: ...

# tools/ — the ONE tool seam the runtime sees. The sub-systems are internal.
class Toolset(Protocol):
    def schemas(self) -> list[ToolSchema]: ...
    def dispatch(self, name: str, args: dict) -> str: ...
    def fire_hook(self, event: HookEvent, payload: dict) -> Decision: ...

# memory/ — one conversational store; retrieval behind the interface.
class MemoryStore(Protocol):
    def recall(self, query: str, k: int) -> list[Memory]: ...
    def remember(self, turn: Turn) -> None: ...

# persona/ — pure. No model, no I/O.
class PersonaCompiler(Protocol):
    def compile(self, profile: Profile) -> Persona: ...   # Persona = system_text, temperature, guards
```

**Store decision (3 → 2, deliberately):** the three stores were chat SQLite, the web `ExperienceStore`, and the harness `Library`. The rebuild collapses the first two into `memory/` and keeps the third as `LessonStore` inside `learning/`. Two domain stores, not one: turns and lessons have different schemas and retrieval semantics. The old "three memory stores" problem is solved by deleting the duplicate conversational store, not by forcing lessons into it.

---

## 4. Build order (dependency order)

```
P0 scaffold → P1 llm → P2 events → P3 memory → P4 tools → P5 persona
→ P6 learning → P7 body → P8 runtime → P9 api/cli → P10 delete core
```

---

## 5. Phases

### Phase 0 — Scaffold and floor · S

**Goal:** an importable `april/` package with a fake-everything test floor.

**Build**
- `april/__init__.py`, `april/__main__.py` (the new self-check: `python3 -m april`).
- `april/config/settings.py` — one `Settings` dataclass from env/YAML.
- `april/testing/fakes.py` — `FakeModel`, `FakeMemory`, `FakeTools`, `FakePersona`, `FakeEvents`, `FakeDevice`.
- `pyproject.toml` — fix packaging: `packages.find.include = ["april*"]`. **Do not add the console-script entry yet** (it lands in Phase 9).

**Verify:** `python3 -m april` asserts the scaffold imports and every fake satisfies its Protocol. `python3 -m core.agent` still green.

**Exit:** new package exists, old app untouched, fakes work.

---

### Phase 1 — `llm/` (the `Model` seam) · S

**Goal:** one LLM path.

**Build**
- `april/llm/base.py` — `Model` (`complete`, `stream`), `ChatRequest`, `ChatReply`, `Delta`.
- `april/llm/openai.py` — `OpenAIModel`: port sync/async/streaming from `core/agent.py` (~L231–238, L533, L735), usage + cost.
- `april/llm/fake.py` — `FakeModel`: scripted text and tool calls.
- **Adopt in old code:** `core/agent.py` and `core/memory_extractor.py` switch to `april.llm.Model` **through a thin adapter**. Do not edit `core/agent.py` beyond that adapter if it carries in-flight user work.

**Verify:** `FakeModel` returns scripted replies; the OpenAI adapter has a contract test that skips without a key; the old self-check stays green (byte-compatible streaming).

**Exit:** every model call goes through one interface.

`shortcut:` only the OpenAI-compatible adapter ships; add a second provider when a second provider is actually needed.

---

### Phase 2 — `events/` · S

**Goal:** one bus, one logger.

**Build**
- `april/events/bus.py` — `EventBus`: `emit`, `subscribe`, ring buffer. Typed events with `event_type` always set (this is where the `_emit_error` / `_emit_status` bug dies).
- `april/events/log.py` — the one logger; sinks: `RingBufferSink`, `FileSink`.
- **Adopt in old code:** `core/events.py` re-exports the new bus. Delete `core/logger.py`, `core/logging.py`, `core/backend_logger.py`, and `core/ipc_logger.py` once no caller remains.

**Verify:** in-memory sink replay test; a regression test that emits an error event through the old path and asserts it arrives (would have caught the bug).

**Exit:** one event path; one logging path.

---

### Phase 3 — `memory/` · M

**Goal:** one conversational store. `LessonStore` stays a domain store in `learning/` (see §3).

**Build**
- `april/memory/base.py` — `MemoryStore`: `recall(q, k)`, `remember(turn)`, `session(id)`.
- `april/memory/sqlite.py` — port `core/database.py` + `core/session.py`; read-compatible with `data/april_memory.db`.
- `april/memory/extract.py` — port `core/memory_extractor.py` onto `april.llm.Model`.
- `april/memory/fake.py` — in-memory store for the floor.
- `core/experience.py` is retired into `memory/`; `core/harness/library.py` becomes the `LessonStore` adapter inside `learning/` (Phase 6).

**Verify:** recall/remember ordering; extraction with `FakeModel`; a migration test reading the existing SQLite file.

**Exit:** one conversational retrieval home. (3 → 2, deliberately.)

`shortcut:` sqlite + keyword ranking only; the `vector` adapter is a later swap behind the same seam.

---

### Phase 4 — `tools/` (the big port) · L

**Goal:** one `Toolset` over the ported ZCode extension layer (decision A).

**Build, in this order (each independently verifiable):**

| Sub | Build | Ported from |
|---|---|---|
| 4a | `tools/base.py` — `Toolset`: `schemas()`, `dispatch()`, `fire_hook()`; `ToolSchema`, `ToolResult` | new |
| 4b | `tools/mcp/` — stdio + http adapters, JSON-RPC, reconnect, OAuth hooks, registry CRUD | `core/mcp_client.py`, `core/mcp_registry.py` |
| 4c | `tools/skills/` — frontmatter parser, safe-key filter, **`scope: user\|module:<name>`**, symlink rejection | `core/feature_adapter.py` (skills), `ZCode/.../adapters/src/skills/*` |
| 4d | `tools/hooks/` — 7-event runner, command/process adapters, trust admission, exit-2 blocking | `ZCode/.../core/src/hooks/*` |
| 4e | `tools/plugins/` — manifest loader, `plugin:<name>:<kind>` namespacing, component enumeration. Loader only; bundled plugins are reference | `ZCode/.../adapters/src/plugins/*` |
| 4f | `tools/subagents/` — profile parser, dispatch, isolation | `ZCode/.../subagent/*` |
| 4g | `tools/builtin/` — shell (port `tools/shell.py`), file read/write | `tools/shell.py` |
| 4h | `tools/registry.py` — the aggregator that implements `Toolset` | new |

**Security is not optional in this phase.** Port these semantics with dedicated tests:
- symlink-escape rejection in skill scanning;
- sensitive-value gating in MCP variable templating;
- workspace-hook trust admission.

**Verify:** per-adapter contract tests; the security tests above; `test_mcp_client.py` / `test_mcp_registry.py` migrate here. `core/feature_adapter.py` retires at the end.

**Exit:** the runtime sees three methods; six sub-systems live behind them.

---

### Phase 5 — `persona/` · M

**Goal:** the per-response compiler, pure.

**Build**
- `april/persona/kernel.py` — port `core/harness/personality.py` (OCEAN, facets, `compile`, `derive`, PSY).
- `april/persona/profiles.py` — `ProfileSource`: `file`, `deployed`; loads `profiles/*.json`.
- `april/persona/guards.py` — `Persona.guards.check(action)`: public-action and secret gates from `core/harness/judge.py`.
- Keep `personality.ts` as the bun cross-check fixture.

**Verify:** golden compiled text (byte-stable); the existing bun cross-check; guard tests (blocked public action, secret refusal).

**Exit:** `core/harness/personality.py` and `core/ade/` dissolve into `persona/`.

---

### Phase 6 — `learning/` (the reusable engine) · L

**Goal:** the Harness2 loop, generic over `Learnable`, native (no subprocesses). **Outcome:** the engine can compose and persist new workflows.

**Build**
- `april/learning/target.py` — `Learnable` (`read/edit/validate/evaluate`) + three adapters:
  - `WorkflowTarget` — port `core/harness/tree.py` + `core/substrate/` + `core/components/` into **one** surface;
  - `MemoryTarget` — distils lessons into `memory/`;
  - `DeviceTarget` — tunes a device config from telemetry.
- `april/learning/loop.py` — port `recursion.py` (propose/aggregate/compose).
- `april/learning/selection.py` — blind selection + experience distillation.
- `april/learning/library.py` — `LessonStore` (port `core/harness/library.py`), OCEAN cosine ranking.
- `april/learning/judge.py`, `ledger.py`, `context.py`, `prompts.py`, `runmark.py`, `spec.py` — port from `core/harness/` (minus personality, which is Phase 5).
- `april/learning/engine.py` — `Engine(target, model, library).run(trace) -> Lesson | None`.
- `april/learning/workspace.py` — a small file workspace (read/write/list) so the proposer is a **native** agentic loop over `llm.Model`, replacing `proposer.py`'s `claude` / `opencode` channels.

**Verify:** `FakeTarget` + `FakeModel` → assert the loop, the ledger identity `R=4k/P=3k` (or `5k/4k` with PSY), and a valid lesson. The old zcode benchmark becomes a `WorkflowTarget` test with no subprocess.

**Exit:** `core/harness/` fully ported; `zcode_adapter.py`, `zcode_cli_stub.py`, and the subprocess channels are gone. `Engine` is instantiable per target.

---

### Phase 7 — `body/` · S

**Goal:** one read-only limb.

**Build**
- `april/body/base.py` — `Device`, `Telemetry`, `DeviceSource`.
- `april/body/esp32.py` — HTTP telemetry read.
- `april/body/fake.py` — `FakeDevice`.

**Verify:** `FakeDevice` parse test; a live read is opt-in and skipped offline.

**Exit:** telemetry is a module the runtime can call; no actuation.

---

### Phase 8 — `runtime/` (the composer) · M

**Goal:** the God object, correctly scoped — sequence only.

**Build**
- `april/runtime/agent.py` — `Runtime.respond/stream`: the turn: recall → compile persona → schemas → complete → guard → dispatch → remember → events → learn.
- `april/app.py` — the composition root: builds every adapter, holds the `engines` dict, returns `Runtime`.
- **Architecture guard** — a self-check that scans `april/runtime/*.py` and fails on forbidden imports (`openai`, `sqlite3`, `subprocess`, `yaml`) and on importing any concrete adapter instead of a `base` Protocol.

**Verify:** all fakes → assert the turn sequence, guard ordering, and that `policy.learn_targets` invokes the right engine. The architecture guard passes.

**Exit:** a full turn works with zero network, zero model, zero device.

---

### Phase 9 — `api/` + `cli/` · M

**Goal:** thin front doors; one of each.

**Build**
- `april/api/` — port `core/api.py` routes onto `Runtime`; SSE sink. **Remap or retire `/api/experience/*`** (the `ExperienceStore` moved into `memory/`).
- `april/cli/` — **one** TUI: **Textual selected; the curses `tui.py` is deleted**.
- `main.py` imports `april.app.build`.
- Add the console-script entry: `[project.scripts] april = "april.cli.main:main"` (deferred from Phase 0).

**Verify:** API smoke test; headless TUI render test; the old web shell still talks to `/api`.

**Exit:** `main.py` runs the new stack; the third entrypoint (`cli/main.py`) is deleted.

---

### Phase 10 — Delete `core/` · S

**Goal:** one package, one entrypoint, one self-check.

**Build**
- Remove `core/`, `tools/` (root), and the retired tests.
- `pyproject.toml` final; `requirements.txt` unchanged (only `openai`, `python-dotenv`, `pyyaml`, `textual`).
- Update `CLAUDE.md` doc set; record the rebuild in **Done**.

**Verify (the definition of done)**
- `python3 -m april` green, offline — no model, no network, no device.
- `grep -r "core\." april/` → nothing.
- `grep -r "import harness2\|from harness2"` → nothing.
- No imports of `ZCode` or its runtime anywhere.
- No duplicate concepts: one LLM seam, one tool seam, one conversational memory store, one workflow surface, one logger, one TUI, one entrypoint.
- Scorecard: 4.3 realized.

---

## 6. Risk register

| Risk | Phase | Mitigation |
|---|---|---|
| Runtime re-bloats into `agent.py` | P8 | The architecture guard is a test, not a guideline. |
| ZCode port drops trust semantics | P4 | Dedicated security tests for symlink, sensitive values, hook trust. |
| Streaming behaviour changes on adoption | P1 | Byte-compatible contract test against the old path before deleting it. |
| SQLite schema break | P3 | Read-compatible migration test against the existing file. |
| Learning loop regresses offline | P6 | Port the ledger identity assertion first; it fails loudly. |
| Scope creep into the full ZCode runtime / vector search / actuation | all | Deferred list below. |

---

## 7. Deferred (deliberate corners)

- **No vector retrieval** — sqlite + keyword now; `vector` adapter later.
- **No ZCode turn machine / model provider / UI** — extension layer only.
- **No bundled marketplace plugins** — the loader is ported; the 35 bundled plugins are reference.
- **No device actuation** — telemetry read only.
- **No second LLM provider** — OpenAI-compatible only.
- **No plugin install UX** — manifest loading and local dirs only.

---

## 8. Definition of done

- `python3 -m april` green offline.
- One of each: LLM seam, tool seam, conversational memory store, workflow surface, logger, TUI, entrypoint.
- The runtime contains only orchestration; the architecture guard proves it.
- `Engine` runs against all three targets from `app.py`.
- The old v1 plan is marked superseded and no code imports `harness2` or `ZCode`.

## 9. First slice

**Phase 0 + Phase 1 together:** scaffold `april/`, the fakes, and `llm/`; make `core/agent.py` and `core/memory_extractor.py` use it through a thin adapter; prove `python3 -m april` and `python3 -m core.agent` both green. One working `Model` seam, one deleted duplication, a stable floor for everything after.
