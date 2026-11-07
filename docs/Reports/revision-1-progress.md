# APRIL — Revision 1 Close-out Report

**Date:** 2026-11-07
**Branch:** `feature/textual-tui` (HEAD `34f98e8`)
**Basis:** `~/.opencode/plan` (authoritative), `docs/spec/phase-plans.md`, direct survey of `core/`, `web/`, and the test suite (55 pytest, 31 offline self-check pins).

This is the first-revision progress report: what is done, what is partial, and
which adapters already exist for attaching ADE features to a frontend UI.

---

## 1. Master plan progress (P0–P7)

| Phase | Scope | Status | Done | Evidence |
|---|---|---|---|---|
| P0 | Repair + consolidate Harness² engine | ✅ Complete | 100% | `c8ff84b`, `ece81b0`; one driver from `run_task` to `adapter.execute()`, ledger identity pinned |
| P1 | ZCode substrate + authored mini-tasks | ✅ Complete | 100% | `1143293`; `ZcodeAdapter`, benchtasks, stub sweeps |
| P2 | Personality kernel (constraint mode) | ✅ Complete | 100% | `eacd637`; `personality.py` + TS mirror, profiles-as-data |
| P3 | PSY third learnable axis | ✅ Complete | 100% | `304438a`; R=5k/P=4k proven |
| P4 | Scale-out contract | ✅ Complete | 100% | `071365b`; third profile = one JSON, zero code |
| P5 | ADE deploy + runtime persona | ✅ Complete | 100% | `61ed7b1`, live acceptance `39c8955` |
| P6 | Facet grain (deferred item promoted) | ✅ Complete | 100% | `4e4ba4c`, `86eb23f`, `b126d6f` |
| P7 | P7-1…P7-9 close-outs | ✅ Complete | 100% | `c83238e` … `34f98e8` |

**Plan gates: all green.** 31 offline self-check `ok:` pins, 55 pytest, ZCode
porcelain clean.

Two external items remain open by choice, not omission:

- GLM 5.3 Flash paid live leg — returns 402 on the account's Free plan; deferred.
- Live zcode-binary sweep — no `zcode` binary on `PATH`; the harness sweep runs on the process-boundary stub.

---

## 2. Sub-feature tables

### 2.1 Harness² engine (`core/harness/`)

| Sub-feature | Implemented | Working | Remark |
|---|---|---|---|
| Recursion modes agg/seq/both (`run_mode`) | ✅ | ✅ offline pins | k INV + k DOM + k PSY scheduling |
| Ledger R/P exactness assert | ✅ | ✅ | holds on the proposer-fallback path |
| Run markers (`.adapt_complete`, fingerprint resume) | ✅ | ✅ | dead-dir guard via `exists` |
| Selection (+ neutral fallback counts as ok) | ✅ | ✅ | finals / trivial / selection paths |
| Library OCEAN-cosine retrieval, floor 0.95 | ✅ | ✅ | transferable gate |
| Judge deterministic gates + trait checklist | ✅ | ✅ | two hard gates, rest attested |
| Proposer channels: claude_cli / opencode / dry | ✅ | ✅ / ✅ stub / ✅ | opencode guarded (`OPENCODE_DIR`); upstream #13 ported |
| Tree validate (surfaces, caps, `persona_cap`) | ✅ | ✅ | loud refusals |
| Sweep drivers `run_sweep` / `run_sweep_zcode` | ✅ | ✅ offline | real-binary sweep blocked (no zcode bin) |
| Multi-seed driver parameter | ✅ | ✅ | `seeds=None` is byte-identical |
| Legacy-lesson backfill | ✅ inert | ✅ dry-run pins | precondition false: no legacy library exists |

### 2.2 Personality kernel

| Sub-feature | Implemented | Working | Remark |
|---|---|---|---|
| `compile_persona` / `derive` / `bandOf` | ✅ | ✅ bun cross-check byte-equal | |
| Facet grain: registry, aggregation, 30 facets | ✅ | ✅ | `mid` band silent by design |
| `FACET_CONDITIONS` + directives + cap 3000 | ✅ | ✅ | cap enforced in `validate()` |
| Degraded (out-of-band) vectors | ✅ | ✅ | authored-only text, PSY off |
| TS mirror (`personality.ts`) | ✅ | ✅ | ocean + facet pins |
| Profiles: april / user / verifier / ridealong / facetfull | ✅ | ✅ | data-only; zero-code scan clean |

### 2.3 ADE + runtime persona (`core/ade/`)

| Sub-feature | Implemented | Working | Remark |
|---|---|---|---|
| `deploy` (validate / stamp / fingerprint / index) | ✅ | ✅ live | tamper refusal pinned |
| `boot` (role-resolved c1/c2, temperature from `derive()`) | ✅ | ✅ live on both surfaces | |
| ActionGuard machine gates | ✅ | ✅ live, 0 false positives | push/secrets refusals observed live |
| Local userContext (facts as count only) | ✅ | ✅ | text never committed or logged |
| View contracts + headless renders (`views.py`) | ✅ | ✅ offline pin | **not yet exposed over HTTP** |
| `python3 -m core.ade` CLI | ✅ | ✅ | |
| TUI widgets over the contracts | ⏸ parked | n/a | product decision: not a priority |

### 2.4 Memory

| Sub-feature | Implemented | Working | Remark |
|---|---|---|---|
| SQLite store + schema migration | ✅ | ✅ 55 tests | |
| Content-hash dedup (unique index) | ✅ | ✅ | earliest row kept |
| Incremental extraction (no quadratic re-send) | ✅ | ✅ | skips LLM when nothing is new |
| Retrieval augmentation into context | ✅ | ✅ | |

### 2.5 Subagents

| Sub-feature | Implemented | Working | Remark |
|---|---|---|---|
| Thread-pool dispatch, futures, parallel | ✅ | ✅ tests | |
| Enforced `dispatch_and_wait` timeout | ✅ | ✅ | task survives past timeout |
| Per-subagent serialization lock | ✅ | ✅ | |

### 2.6 MCP

| Sub-feature | Implemented | Working | Remark |
|---|---|---|---|
| Runtime MCP **client** (spawn/connect stdio/HTTP servers) | ❌ | — | no client code in `core/` |
| MCP **server** (expose APRIL tools via MCP) | ❌ | — | |
| MCP config / permission surface in the agent runtime | ❌ | — | |
| Benchmark task `benchtasks/mcp/wiring` | ✅ | ✅ offline | paper exercise: wiring doc from a catalog snapshot |
| Harness component prose mentioning MCP | ✅ | ✅ | prompt text only (`plugins`, `tool_guidance`) |
| Web UI MCP screen | ⚠️ placeholder | — | nav lists `MCP`; `ModuleScreens.tsx` is a placeholder panel |
| ZCode-side MCP (vendored app) | ✅ | — | substrate's own config, not driven by APRIL |

**Verdict: MCP ≈ 5% — represented in benchmarks and the UI shell only. No runtime integration. This is the largest genuine gap.**

### 2.7 Web backend + frontend

| Endpoint / piece | Implemented | Working | Remark |
|---|---|---|---|
| `GET /healthz` | ✅ | ✅ | |
| `GET /api/events` (SSE via `EventBroadcaster`) | ✅ | ✅ web `EventSource` wired | |
| `POST /api/chat` (streaming) | ✅ | ✅ | |
| `POST /api/sessions/clear`, `GET /api/context` | ✅ | ✅ | context pane still a placeholder |
| Browser endpoints (open/navigate/action/screenshot/content/close/sessions) | ✅ | ⚠️ untested | no coverage in the 55 tests |
| Frontend Chat view | ✅ | ✅ | |
| Skills / MCP / Hooks / Terminal screens | ⚠️ placeholders | — | |
| Auth on the API | ❌ | — | backlog item |
| Sessions list / history endpoints | ❌ | — | |

### 2.8 TUI (parked by decision)

| Piece | Status |
|---|---|
| Textual app + widgets (incl. `HarnessStatusWidget`) | parked, functional |
| IPC server (TUI ↔ logger) | parked |
| Curses `core/tui.py` | dead code, kept |

### 2.9 Benchmarks / benchtasks

| Piece | Implemented | Working |
|---|---|---|
| `benchtasks/device-control/read-write` | ✅ | ✅ offline |
| `benchtasks/mcp/wiring` | ✅ | ✅ offline |
| `benchtasks/network` | ✅ | ✅ offline |
| zcode process-boundary stub (`zcode_cli_stub.py`) | ✅ | ✅ |
| Real zcode binary sweep | ❌ | binary absent |

---

## 3. Adapter report (modularity for ADE + frontend attach)

Three adapter families exist today, plus the frontend transport layer.

### 3.1 Execution adapters — `AgentAdapter` protocol (`core/harness/spec.py`)

| Adapter | File | Working | Attach path |
|---|---|---|---|
| `DemoAdapter` | `demo_adapter.py` | ✅ offline pins | deterministic CI/demo substrate |
| `ZcodeAdapter` | `zcode_adapter.py` | ✅ stub / ⚠️ no real binary | spawns the zcode CLI with an isolated run-home |
| `AgentRuntime` (APRIL substrate) | `core/agent.py` | ✅ dry + ✅ live (beam:cloud) | `APRIL_EXECUTOR=dry\|live` |
| `_DryProposer` | `execution_manager.py` | ✅ | fail-closed offline proposer |

### 3.2 Surface adapters — `SurfaceSpec` (what the engine may edit)

| Surface | File | Working |
|---|---|---|
| `APRIL_SURFACE` | `core/substrate/april.py` | ✅ |
| `ZCODE_SURFACE` | `core/substrate/zcode.py` | ✅ |
| `DEMO_SURFACE` | `demo_adapter.py` | ✅ |

### 3.3 Proposer channel adapters (model transport)

| Channel | Working | Remark |
|---|---|---|
| `claude_cli` (Vertex) | config-only | needs Vertex env |
| `opencode` (bun) | ✅ fail-closed | activates with a checkout |
| `dry` | ✅ | offline default |
| GLM / OpenAI-compatible | ⚠️ | reaches the agent loop directly, **not** as a harness Proposer channel; 402 on Free plan |

### 3.4 Frontend transport adapters (attach-to-UI)

| Adapter | Chain | Working |
|---|---|---|
| Events | `EventBus` → `EventBroadcaster` → SSE `/api/events` → web `EventSource` | ✅ |
| Chat | REST `/api/chat` streaming → `chatStore` | ✅ |
| Browser | REST `/api/browser/*` → `BrowserService` (Playwright) | ✅ code / untested |
| TUI | `EventBus` → IPC socket → Textual widgets | parked |
| ADE views | `views.py` pure JSON view models → **no transport yet** | ⚠️ gap |

### 3.5 Second-revision gaps for the modular-attach goal

1. **No `/api/ade/*` transport** — `surfaces_view()`, `persona_view`, and library rows exist but have no REST/SSE surface, so the web UI cannot display ADE state. Cheapest high-value win.
2. **No adapter registry** — adapter choice is hardcoded in `_harness_spec` / `run_sweep_zcode`; a name → adapter factory would make new substrates plug-in.
3. **MCP runtime adapter absent** — a from-scratch feature (client, config, permission gating via the existing ActionGuard seams).
4. **Tool registry is hardcoded** (`ShellTool` only); `BrowserService` is not in the agent's `tool_map`.
5. **Sessions/history endpoints + API auth** — backlog.
6. **Packaging** — `packages.find include=["april*"]` matches no package; non-editable installs ship a broken console script.

**Suggested order:** (1) ADE REST surface → (2) adapter registry → (4) tool registry incl. browser-as-tool → (3) MCP client, folding (5)/(6) in where convenient.
