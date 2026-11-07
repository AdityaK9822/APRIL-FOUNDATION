> **Snapshot — the project's central idea.** Copied from the repository's local `CLAUDE.md` on 2026-11-07 (branch `feature/textual-tui`, HEAD `34f98e8`). `CLAUDE.md` remains the working copy; this file is the archived statement of intent.

# APRIL

**An assistant with a body — and the body is the networked physical world.**

APRIL's limbs are the machines it can reach over the network: the computer it
runs on, the software on that computer, and everything wired into the same
network — cameras, sensors, appliances, microcontrollers, robots, and the
fabrication machines that build new hardware. Wifi is how it reaches most of
them today.

The intelligence is an LLM; the point is that it is *attached to something*. An
assistant that can only answer is a chatbot. APRIL **senses** the world (a
camera it can see through, an ECG it can read), **acts** on it (a light it
switches, a robot arm it drives, a printer it runs), and **works the network
itself** (discovering devices, auditing the wifi it is authorized to test). It
can take a request all the way to a physical result — building a PCB in real EDA
software, milling it on the CNC, printing its case and flashing it is one such
result, not the whole of what APRIL is.

Local models run the work they can handle; larger or cloud models are used when
the work genuinely needs them. **Keeping your work on your machine is a value
here, not an afterthought** — the default is local, going off-box is a decision,
and there is no telemetry.

---

## Maintenance — read this first

**This file is the single source of truth for APRIL's idea, scope, and roadmap.
There are no other project idea docs** — `docs/spec/` carries the standing phase
plans and future specs (see "Doc set" below), and nothing else lives under
`docs/`.

It describes **what APRIL is and what it should do** — deliberately not how. File
layout, module boundaries, protocols, and code structure are settled when each
file is written, not here.

Whenever a change alters behaviour, the tool set, the config surface, or the
plan, update this file **in the same change**:

1. Correct any section the change invalidated.
2. Move the roadmap item to **Done** with a date, or add a new deferred item.
3. Leave `# ponytail:` comments in code for deliberate corner cuts, naming the
   ceiling — the point at which the shortcut stops working.

A change that ships without updating this file is not finished.

## Doc set

`docs/` is intentionally minimal:

- **`CLAUDE.md` (root)** — the single source of truth for the idea, scope, and
  roadmap.
- **`docs/spec/phase-plans.md`** — the standing, detailed plan for phases P0–P7
  (execution items, acceptance gates, risks). Live item status is tracked beside
  it; this doc is the reference, not the checkbox.
- **`docs/github_instructions/create_issues.md`** — operational runbook.

Future specs are written **spec-first** into `docs/spec/` — the section-8 docs
(`zcode-substrate.md`, `persona-dimension.md`, `ocean-profile-schema.md`) land
there in P4/P5, before the code that depends on them. The 22 superseded
implementation documents were removed 2026-10-31, superseded by the phase
plans; they remain in git history (`git log --diff-filter=D -- docs/`, and
`git log --diff-filter=D -- IMPLEMENTATION.md` for the root file).

### The Harness² engine

The recursive harness-improvement engine under `core/harness/` is the
**Harness² method code, vendored from Google (Apache-2.0) and installed
locally** — it is a dependency the method needs, not a claim of authorship.
Versions: prompts `VERSION='harness2-2'` (`core/harness/prompts.py`), run markers
`VERSION=1` (`core/harness/runmark.py`). Upstream checkout `harness2/` is pinned
at `6868817` (2026-11-02); the P7-8 re-sync found no newer upstream commits,
ported upstream #13 (OpenCode session errors beat a stale `answer.md`), and
deliberately did NOT port #110 (`Stage.execution_failed`): the offline
`_DryProposer` fails closed by design, so that flag would mark every dry mode
not-ok. #33 is already present. APRIL rides on it through its own
substrate (`core/substrate/` — the editable surface schema) and base harness
(`core/harness/basetree/april/`). The engine runs offline in `dry` mode: `APRIL_EXECUTOR=dry`
(default) gives structural rollouts with **no model, no network, and no device**,
and the standing self-check is `python3 -m core.agent`.

### The personality kernel (P2)

The OCEAN engine is **profiles as data + `compile_persona` as the compiler**, in
constraint mode: a profile shapes how the editor thinks, writes and weighs
evidence *within* the mission — it never relaxes a mission rule and never reaches
the blind selector. The pieces:

- **`core/harness/personality.ts`** — the TypeScript reference implementation,
  archived verbatim from the master plan's Appendix A **except** two redactions:
  `USER_PROFILE`'s provenance note and APRIL's `userContext` facts, which live in
  the git-ignored `.ade/etc/persona-user-context.md` (the ocean vector itself is
  the committed `profiles/user.json`).
- **`core/harness/personality.py`** — the faith-port (`validate`, `bandOf`,
  `clamp`, `adjust`, `derive`, `compile_persona`, `similarity` cosine over
  `ocean/100`). JS `Math.round` is half-up; python's is banker's — every derived
  integer goes through `_round` (`floor(x+0.5)`), pinned by APRIL proactivity
  66.5 → **67**. `personality.demo()` (runs in `python3 -m core.agent`) asserts
  the pins and **byte-equal cross-checks the compiled preset + derived params
  against the TS through bun**.
- **`profiles/*.json`** — `april`, `user`, `verifier`; schema of record is
  `PersonaConfig` (+ `id` = filename stem). Committed profiles carry **no
  `userContext`** and the loader rejects it at load time. `HarnessSpec.profile`
  names one; `resolve_prompts` fills the `persona_guidance` slot (itself a
  `HarnessPrompts` slot riding the bad-word filter), and `prompts.py` embeds it
  in the five proposal missions (`propose`/`aggregate`/`compose_round`, tts and
  adapt alike) — never in select/experience.
- **`PERSONA_CLAUSE_CAP = 1600`** (`tree.py`) — persona text may enter an
  always-on file only under a `## persona:` heading, and tagged sections are
  capped per file; over-cap trees are rejected by `tree.validate` and ride the
  existing `_accept_tree` retry path (`STAGE_ATTEMPTS=2`, untouched). Longer
  persona prose belongs in an on-demand skill. # ponytail: the cap value is
  self-judged (~1500–2000 band) — much larger and this is a second system
  prompt wearing a different name.
- **Sampling rule**: `temperature = 0.2 + 0.7·O/100` on the active profile
  (`derive(p).temperature`; APRIL boots at 0.69).
- **Ledger**: `expected_rollouts(k, n_dims=2) = (2+n_dims)·k` and
  `expected_sessions(k, n_dims=2) = (n_dims+1)·k` — defaults keep R=4k/P=3k;
  P3's PSY dimension passes `n_dims=3` (5k/4k) with no caller change.
- **Lessons carry `{profile_id, ocean}`** — run provenance attached in
  `selection._distil` from the adapter's active profile, written into library
  entries, `INDEX.md`, and the root `EXPERIENCE.md` (new nullable Persona
  column). P3's cosine gate now uses that vector for retrieval.

### The PSY dimension (P3)

Personality is a **learnable third axis**, not just a constraint. Profiles stay
data; the dimension is scheduled only when a profile is active:

- **Activation**: `prompts.active_dims()` returns `(INV, DOM, PSY)` iff
  `BenchPrompts.psy_levers` is non-empty. `resolve_prompts` fills that slot from
  `personality.psy_mechanism(profile)` — `derive()` params only, never raw
  traits — so a persona-free run keeps R=4k/P=3k and a profile run earns
  R=5k/P=4k (`Ledger.n_dims=3`). `propose(..., "PSY")` raises without levers.
- **Prompts**: `_PSY_TEMPLATE` / `_TTS_PSY` own expression only (tone, pacing,
  delegation, verification temperament) and explicitly disclaim INV/DOM lanes;
  `aggregate`/`compose_round` name the 3k candidate set when PSY is active.
- **Judge**: `judge.trait_checklist` runs **after** selection. Exactly two checks
  are machine-decidable — no unapproved public action (an `user approved`
  marker satisfies it) and no stored/echoed/logged secrets; every protocol,
  extra rule and threshold rule is recorded `attested` with `ok=None` rather
  than pretending to verify prose. `score_after_selection(profile=...)` attaches
  the checklist beside `adapter.score`, and stays `None` when selection failed.
- **Attribution**: `selection.edit_attribution` intersects
  `ComponentRegistry.diff` with each stage's dimension. A lesson whose changed
  components are all PSY is `transferable: false`; anything touching INV/DOM
  transfers. `_annotate_edits` writes a `dimensions:` line per edit record.
- **Library**: `Library.load_context(profile=...)` ranks by OCEAN cosine and,
  below `SIMILARITY_FLOOR = 0.95`, withholds non-transferable lessons (including
  from the browse index). Persona-free libraries keep recency order.
  Measured: april↔user 0.9667 passes; april↔verifier 0.9111 is gated unless the
  lesson says `transferable: true`.
- **Namespaces**: `ns()` gains `-P<persona>` (`zcode-zcode-agg2-Papril`) and
  `run_sweep(profiles=...)` fans one call over profiles with a per-profile
  adapter spec, run namespace and library root. Seeds remain one sweep each —
  the namespace already carries `-s<seed>`, so a multi-seed run is a caller loop,
  not a new fan-out shape. # ponytail: revisit if P4 needs one-command seeds.

### The scale-out contract (P4)

Personality scales by **data, not code**: `load_profiles()` globs
`profiles/*.json` and the id is the filename stem, so a third persona is one JSON
file. `profiles/ridealong.json` (low N, high A, mid E) is the proof — no file
under `core/harness/` names it, and it runs a 1-task sweep as
`zcode-zcode-agg1-Pridealong` with R=5=5k, P=4=4k and its own library root.
`docs/spec/ocean-profile-schema.md` is the format contract: fields, band edges,
the exact derivation formulas, validation, the 0.95 transfer gate, and how to
add a profile.

- **Out-of-band vectors degrade, never lie.** The band table covers the five
  OCEAN traits; a profile carrying signal outside it — today `facets`, the
  deferred 30-facet expansion — still loads and runs, but `compile_persona`
  emits authored c1 text only (no OCEAN directives, Voice/Honesty lines, or
  threshold rules) and `psy_mechanism` returns `""`, so `active_dims` stays 2-D
  and the ledger keeps R=4k/P=3k. The ceiling is a `# ponytail:` note on
  `personality.out_of_band`.
- **Typos fail loudly.** Unknown top-level, `ocean` or `relationship` keys are
  refused at load time, so adding a profile cannot silently compile nothing.
- **Deferred:** 30-facet Big Five expansion, and a nearest-profile cosine
  backfill for legacy library lessons (needs a real legacy library first).

### Substrates — the surfaces the engine edits

Two substrates exist:

- **`april`** (`core/substrate/april.py`) — the vendored-layout surface; the dry
  executor exercises it offline.
- **`zcode`** (`core/substrate/zcode.py`) — the ZCode CLI's editable surface,
  defined from its own docs: workspace `AGENTS.md` as the one always-on file,
  `.agents/skills/<name>/SKILL.md` (frontmatter `name` must match the directory),
  `.zcode/agents/<name>.md` sub-agents, `.zcode/config.json` hooks against the
  seven-event v2 list, `.zcode/plugins/<name>/.zcode-plugin/plugin.json`
  manifests, and a `MEMORY.md` seed that the adapter materialises into the CLI's
  own isolated store (never read from the workspace).

The engine never builds or edits ZCode itself — the boundary is the **process
spawn** in `core/harness/zcode_adapter.py`, running ZCode's documented batch
recipe (`--mode yolo`, stream-json) with per-sample isolation
(`ZCODE_DATA_BASE_DIR` + a one-shot env whitelist) into
`core/harness/basetree/zcode/` harness trees. Each authored mini-task under
`core/harness/benchtasks/` forces a deliverable file
(`deliverables/<name>`): a run that ends without it is not delivered. Offline,
the same contract runs against `core/harness/zcode_cli_stub.py` — a
process-boundary stand-in; the real CLI is `APRIL_ZCODE_BIN` and fails closed
without it. `schedule.run_sweep_zcode` is the first full sweep on this surface.

Ponytail ceilings on the zcode substrate (each is a deliberate corner with a
named ceiling):

- workspace hook files are **schema-validated but untrusted** — ZCode docs 09
  say the CLI does not trust workspace hooks yet, so a check the run must not
  skip is a `scripts/<name>.py` step, not a hook. Ceiling: trust wiring.
- `always_on_max=60_000` and `memory_max=40_000` are **self-judged caps**
  (zcode books no documented limits); they exist so always-on prose cannot
  silently become the whole context.
- `memory_project_key` replicates a zcode-internal contract
  (`memory/project-root.ts`, slug-`-`-sha256[:16]); **re-verify on any ZCode
  update** or the memory seed lands in the wrong store.
- plugin-embedded skills are **not recursed** — only manifests are validated.
- `zcode doctor --json` is the one optional gate **not yet wired** (needs a
  real CLI; offline runs the stub, which has no doctor).

Standing principle: **the ADE must itself be an APRIL agent.** The development
environment is built and operated through APRIL's own agent loop — adopted
harness trees, persona profiles, its own library — not as a separately
hand-maintained stack.

Standing principle: **the ADE must itself be an APRIL agent.** The development
environment is built and operated through APRIL's own agent loop — adopted
harness trees, persona profiles, its own library — not as a separately
hand-maintained stack.

---

## Goal

An assistant that reaches out of the terminal and into the physical world —
reasoning about a goal, then sensing, acting, and building through the devices on
its network, without you touching any of them. Fabrication is the deepest case
(a sentence to a finished object), but the same body reads a sensor, watches a
camera, switches an appliance, or audits a network.

The near-term goal is narrower and concrete: prove the body is real with one
small, honest win in each of its modes — see something, act on something, touch
the network, build something — rather than one grand demo.

---

## The three pillars

APRIL acts through three channels. A real project crosses all three, which is
why none of them is optional.

**1. The machine** — the computer APRIL runs on. Shell, files, processes, system
state. This is the substrate everything else runs on top of, and the reason full
system access exists.

**2. The software** — the applications APRIL drives through their APIs, CLIs, or
scripting interfaces. Design tools (EDA, CAD, slicers) where a thing exists
before it is real; vision models that turn a camera frame into something APRIL
can reason about; network and security tooling that turns a radio into an audit.
This is where raw device data becomes intent, and intent becomes a plan.

**3. The devices** — everything physical on the network: cameras, sensors,
appliances, microcontrollers, robots, and fabrication machines. This is where
APRIL stops being software. It is a broad and growing set, catalogued in **The
body** below.

The **network is the connective tissue** — the medium through which the machine
reaches most devices, and, in the security domain, a thing APRIL acts on
directly. Orchestration happens on pillar 1, reasoning and design on pillar 2,
sensing and actuation on pillar 3.

---

## Skills and integrations — the unit of work

Every capability APRIL gains is a deliberate piece of engineering, not a generic
passthrough. Adding a tool means two things:

- an **integration** — the code that speaks that tool's actual language, whether
  that's an API, a CLI, a serial protocol, or gcode;
- a **skill** — the instructions and domain knowledge that teach an agent to use
  that tool *well*, so it produces good output rather than merely valid output.

Skills live in a **skill library** that subagents draw from. A subagent picks up
the skills for the job in front of it.

This is where most of the effort in APRIL goes, and pretending otherwise makes
the roadmap dishonest. Growth is measured in integrations, not features. The
consequence is that capability arrives one tool at a time — and each one should
be genuinely good before the next is started.

---

## APRIL and its subagents

**APRIL is the core agent.** It talks to you, understands the goal, plans the
work, breaks it into tasks, and dispatches those tasks to subagents it controls.
It is the only thing that holds the whole picture.

**Subagents do the specialised work.** Each one owns a domain, pulls the
relevant skills from the library, and reports back. APRIL reviews what comes
back before it takes effect — for software that is quality control, and for a
machine that is about to move, it is the last check before something physical
happens.

**Models:** subagents run on APRIL's reasoning model by default. A subagent
declares its own model only when its work genuinely requires different
capability — vision, code generation. A model enters the system when a subagent
needs it, not before. Starting from reasoning is deliberate: APRIL's own job —
converse, plan, decompose, dispatch, review — is a reasoning job, and a roster
of specialised models collected in advance is an inventory, not an architecture.

---

## The body — the networked world

APRIL's body is not a fixed set of gadgets — it is **whatever is reachable on the
network right now.** Each device is a named limb that carries what it is, what it
can do, whether it's here (online, offline, busy, faulted), and how dangerous it
is (see the safety policy below). APRIL plans against the body it actually has: a
plan that needs the CNC while the CNC is offline is one it should know is
impossible before it starts.

The limbs fall into a handful of domains. This list is **illustrative, not
exhaustive** — the whole point is that new domains slot in the same way. Nothing
here is built yet; the ESP telemetry read is the one early prototype.

| Domain | What APRIL does | Reached via | Status |
|---|---|---|---|
| **Vision / surveillance** | See through cameras; a vision subagent describes and reasons over the feed | IP cameras | Not built |
| **Sensing / medical** | Read ECG, EEG, and other sensors; environmental telemetry | ESP + sensors | Prototype (telemetry) |
| **Home / appliances** | Control wifi appliances — lights, AC, switches, plugs | wifi appliances | Not built |
| **Robotics / custom MCU** | Drive robot arms and other wifi microcontrollers built for a job | ESP / other MCU | Not built |
| **Network / security** | Wifi auditing, device discovery, raw packet work — *authorized testing only* | wifi radio | Not built |
| **Industrial / warehouse** | Replace batch and manual control loops with a reasoning agent | networked PLCs/MCUs | Not built |
| **Fabrication** | Design in software, then print, mill, solder, and flash | 3D printer, CNC, EDA | Not built |

### The ESP32 — the archetypal limb

The ESP32 is not a fixed appliance; it is a **programmable wifi bridge to the
physical world.** Flashed one way it is a telemetry/sensor node; flashed another
it actuates GPIO, drives hardware, speaks to other microcontrollers, or acts as a
radio for network work. The telemetry HTTP server APRIL reads today was the
**first test flash**, not the device's purpose — it is the cheapest, most general
way to grow a new limb, which is why it comes first.

Transport, honestly stated: **wifi / IP is the near-term medium** ("for now, only
networked devices"). Bluetooth, serial/USB, and other radios are deferred until a
project needs them.

---

## Safety stance

Full system access is a **deliberate feature**. There is no sandbox and no
allowlist on command execution — that principle does not change. But a wide body
means more than one kind of mistake, so the policy has three axes.

**Physical risk — per-device.** A bad shell command you undo; a bad CNC command
ruins a workpiece, breaks a machine, or starts a fire. Anything that moves,
heats, cuts, or flashes carries a **risk level the device itself declares**, and
that level sets what it takes to act: an ESP32 blinking an LED and a spindle at
18,000 rpm do not share a policy. The higher the risk, the more the plan is
checked — by APRIL, or by you — before the machine moves.

**Network authorization — the one hard line.** APRIL's network and security
capability (auditing, discovery, raw packet work) is scoped to **networks you own
or are explicitly authorized to test.** APRIL is a workshop assistant, not an
attack platform pointed at third parties; that boundary is not opt-in and not
negotiable.

**Data sensitivity — reading is not always harmless.** Most reads run free, but
some limbs sense private things — a home camera, an ECG. Seeing carries weight
even though it moves nothing: what APRIL captures stays on your machine (the
privacy stance is the point), and a sensing limb over a person or a home is
treated as sensitive, not as free telemetry.

Everything else — the machine, the software, ordinary reads — runs light: free,
no prompt, guards opt-in and off by default.

Run it on a machine you control, a network you are authorized to touch, and next
to a workshop you can reach the power switch in.

---

## Projects — the unit of ambition

"Build a PCB and a case for it" is hours or days of work, across several
machines, with steps that fail and get retried. That does not fit in a
conversation.

A **project** is the long-lived thing APRIL tracks:

- **goal** — what you asked for;
- **plan** — the steps to get there, and which subagent owns each;
- **state** — done, running, blocked, failed;
- **artifacts** — the files it produced: models, gerbers, gcode, firmware;
- **devices** — what it is using, and for how long.

A project outlives the request, the session, and the process. You should be able
to walk away and come back.

**Not built yet.** The near-term step is session memory — enough context to hold
multi-step work together within a conversation. Durable projects come after, and
the design above is written down now so that what gets built first doesn't make
it impossible later.

---

## Running

Runs on the system `python3` with **no venv**. The only hard dependency is
`openai`.

```bash
cp .env.example .env      # then point it at your model
```

That is the whole setup. The terminal front door is what gets built first;
everything else lands later.

## Configuration

APRIL talks to any **OpenAI-compatible endpoint** — a local runtime or a cloud
provider. Which model runs it is your decision, set in `.env`; nothing else in
APRIL changes when you switch.

| Variable | Meaning |
|---|---|
| `OPENAI_API_KEY` | API key for the endpoint (a placeholder for most local runtimes) |
| `OPENAI_API_BASE_URL` | The endpoint |
| `OPENAI_MODEL` | The reasoning model APRIL runs on |

### Harness engine variables

| Variable | Meaning |
|---|---|
| `APRIL_EXECUTOR` | `dry` (default): offline structural rollouts — no model, no network, no device. `live` still fails closed (`NotImplementedError` in `core/agent.py`) until a phase wires it |
| `APRIL_RUNS_ROOT` | Where run dirs live (default: `spec.runs_root`, `runs/`) |
| `APRIL_BUN` | bun binary for the opencode proposer channel (default `bun`) |
| `APRIL_OPENCODE_DIR` | A pinned OpenCode checkout enabling the opencode channel; **empty leaves the channel off** and calls fail as `ProposerError` |
| `APRIL_ZCODE_BIN` | The zcode substrate's CLI binary (SEA build or on-PATH name); **offline self-checks set it to the process-boundary stub**. Read at spawn time with `core/harness/config.py`'s `ZCODE_BIN` as the fallback — zcode rollouts come from `schedule.run_sweep_zcode`/`ZcodeAdapter`, which re-read this per spawn |

No model is named here on purpose. Local is the preferred default and going
off-box is a decision — but that is a preference about *where* the work runs,
not a commitment to a particular model.

The device registry, subagents, and safety policy will define their own
configuration as they are built; those knobs get documented here when they
exist.

---

## House style

- **Laziest solution that works.** Keep the file count small; no new file without
  a real reason. Stdlib before dependencies. One line before fifty.
- **Don't neuter command execution.** Full system access is a deliberate feature.
  Guards on the machine and software side are opt-in, off by default. Physical
  devices are the exception, and the risk policy above says why.
- **Stdlib-only for system access:** `os`, `platform`, `subprocess`. A new pip
  dependency needs a one-line justification in the commit message and an entry in
  `requirements.txt`.
- **Preserve the terminal echo** of every command run — the user must see what
  executed.
- **`# ponytail:` comments** mark a deliberate corner cut and **name its ceiling**
  — the condition under which the shortcut stops working.

### Testing

No framework. If you add non-trivial logic (a parser, a branch, a loop), leave one
runnable check — an `assert`-based `demo()` under an `if __name__ == "__main__"`
guard. Trivial changes need no test.

The self-checks must pass with **no model, no network, and no device attached**.

---

## Roadmap

Two tracks, built in parallel, converging on a proof in each mode of the body.

### Track A — intelligence

- [ ] **Talk.** APRIL connects to the LLM and holds a conversation in the
      terminal. No tools, no agency. This is the floor.
- [ ] **Act.** Tools, and a loop that lets APRIL call one, observe the result,
      and iterate until the request is done.
- [ ] **Skills.** The integration + skill pattern, proven end to end on one real
      tool. Establishes what adding a capability actually costs.
- [ ] **Subagents.** APRIL plans, dispatches to a subagent, reviews what comes
      back. Skills pulled from the library per task.
- [ ] **Memory.** Session context, so multi-step work holds together.

### Track B — body

- [ ] **One limb.** An ESP32 read reliably over the network. The first flash.
- [ ] **The registry.** Devices as named limbs with capabilities, liveness, and a
      declared risk level. APRIL can report the live state of everything attached.
- [ ] **Risk policy.** The three axes above: per-device physical risk, the network
      authorization line, and data-sensitivity on reads.
- [ ] **First actuation.** A physical device does something because APRIL decided
      it should — small, cheap, reversible.

### Where they meet — a proof per mode

The body has to be shown real in each of its modes, not just its most impressive
one. **Read the body** comes first (the registry underlies everything); then one
small, cheap, honest win in each mode. None has to be grand — each just has to be
end to end and unfaked.

0. **Read the body.** APRIL reliably sees and reports the live state of every
   connected device. Unglamorous; it's the prerequisite the registry proves.
1. **See.** A camera feed a vision subagent describes — "what's in front of it?"
2. **Act.** One wifi appliance toggled — a light or an AC, off and on, on request.
3. **Network.** A wifi audit of a network you own — discovery and a clean report.
4. **Build.** One machine, one object: "print me a bracket that holds this board
   at 30°" — APRIL drives CAD, slices it, prints it, tells you when to collect.
   The deepest mode, because it crosses all three pillars.

The north star past these stays **the full build** — design a PCB in EDA, mill
and solder on the CNC, print the enclosure, flash the board — but it is the
horizon, not the first thing to prove.

### Later

- [ ] Durable projects: plan, state, artifacts, and device reservations that
      survive a restart.
- [ ] More limbs, across the domains: cameras, medical/other sensors, more
      appliances, robot arms and custom MCUs, CNC and 3D printers, industrial
      controllers.
- [ ] More integrations: vision models, network/security tooling, EDA, CAD,
      slicers, MCP servers, plugins.
- [ ] ESP beyond wifi: OTA firmware flashing, and a serial/USB or other-radio
      transport as alternatives to the network.
- [ ] Model routing inside APRIL — reasoning, code, and vision models chosen per
      task as subagents come to need them.
- [ ] A backend API, so other frontends and systems can call in.
- [ ] The interface layer: a proper app, voice, and vision. Removed once because
      it was built before the harness underneath it existed; it comes back when
      that is no longer true.
- [ ] Auth and per-request confirmation for any non-local caller.
- [ ] Handle Ctrl-C gracefully instead of a traceback.

### Considered, not doing (yet)

- **A single "brain" containing every model** — image, vision, coding, reasoning
  models assembled up front. Nothing emerges from co-locating models; you still
  need the routing, and you'd have paid for every integration before anything
  calls it. Models arrive when a subagent needs one.
- **Sandboxing or an allowlist on the machine** — conflicts with the full-access
  design goal. Physical devices are handled by the risk policy instead.
- **Cloud-first operation** — local is the default; going off-box is a decision.

### Done

- **2026-11-04 (P7-9)** — Working-tree hygiene, decided path by path (never `-A`).
  The in-flight memory/subagent/TUI/API/web work was green (55 pytest) and coherent,
  so it landed as `47d2426` "Version 1.1" (51 files): incremental extraction +
  content-hash dedup, pooled subagent dispatch, FastAPI backend + browser service,
  TUI harness widget, `scripts/dev.py`, `web/` source (34 files), profiler, tests,
  CHANGELOG, `.env.example`. Its pyproject rewrite kept the real fix: console
  script `april.cli.run:main` -> `main:main`, root modules declared as `py-modules`.
  Hygiene commit `34f98e8`: `.gitignore` now ignores `ZCode/`, `harness2/`,
  `web/node_modules/`, `web/dist/`, `data/*.db*`, `.agents/`, `skills-lock.json`;
  `git rm --cached` untracked `data/april_memory.db` + `-shm`/`-wal` (files stay on
  disk — memory survives, runtime state is unversioned); dead auto-merge workflow
  and tracked `april_os.egg-info/` committed as deletions. Post-check:
  `git status --porcelain` empty, `git check-ignore` verified every class, 31 ok,
  ZCode porcelain empty. **P7 complete.**
- **2026-11-04 (P7-8)** — External gates closed for the harness2 re-sync. The
  `harness2/` checkout is pinned at `6868817` (2026-11-02); `git fetch` found no
  newer commits, so no vendor files were re-copied. Ported upstream #13:
  `Proposer._opencode` folds the JSONL stream and returns a session error
  **before** it will read `output/answer.md`, so a crashed session's stale answer
  cannot become a proposal (self-check 27 stubs the checkout + subprocess; offline,
  no model). Deliberately did **not** port #110 (`Stage.execution_failed`):
  `_DryProposer` fails closed by design, so the flag would mark every dry mode
  not-ok and break the P0 fallback contract — recorded as a `# ponytail:` note on
  `ModeResult.ok`. #33 was already present; #112/#122 are jobbench-only and not
  vendored. GLM 5.3 Flash stayed spend-gated (Free-plan 402), so the live leg ran
  on the free `beam:cloud` fallback: one real ocean-grain loop, deployed-persona
  boot (temp 0.69, 6367-char prompt, local facts as a count only), two read-only
  shell calls and a correct answer, $0.00 paid. Evidence in git-ignored
  `runs/p7-8-live/beam-cloud.md`. Self-check **31 ok**; ZCode porcelain empty.
  `docs/spec/phase-plans.md` carries the P7-8 record.
- **2026-11-04 (P7-7)** — Committed facet-grain profile: `profiles/facetfull.json`
  (all 30 facets, 6 per domain) aggregates by hand-computed half-up means to
  `ridealong`'s vector (60 55 50 85 15), compiles the same OCEAN section and
  derived params (cosine 1.0), and runs a 1-task `-Pfacetfull` sweep with
  R=5/P=4/n_dims=3. Zero harness code change; zero-code scan clean. Self-check
  (30 ok) pins the hand means, kernel purity, and the data-only sweep. CLAUDE.md
  local.
- **2026-11-04 (P7-6)** — TUI view contracts: pure headless render helpers
  `render_persona_summary` and `render_library_view`, plus `surfaces_view()` in
  `core/ade/views.py`; facts never leak into rendered panes (count only).
  Self-check (29 ok) proves terminal-free render. User-owned TUI streams absorb
  via the JSON contracts unchanged. CLAUDE.md local.
- **2026-11-04 (P7-5)** — ZCODE-surface boot retired the empty-c1 ponytail.
  `boot.py` + deploy `_stamp` resolve c1/c2 by role against `SurfaceSpec.always_on`
  (APRIL per-role files; zcode folds both into `AGENTS.md`); zcode deploy stamps
  both `## persona: compiled` and `## persona: protocols` into AGENTS.md, and
  boot returns non-empty c1 with the compiled persona sections plus guardrails
  derived from the profile. Unresolvable/empty raises. Self-check (28 ok)
  covers zcode deploy+boot + an APRIL regression. CLAUDE.md local.
- **2026-11-04 (P7-4)** — Seeds become a driver parameter
  (`run_sweep(..., seeds=...)`): multi-seed runs each single-seed path and
  aggregates per-seed ledger rows (distinct `-s<seed>` dirs under one
  `library_root`); seeds=None is byte-identical to the old single-seed branch.
  Self-check (27 ok). CLAUDE.md local.
- **2026-11-04 (P7-3)** — Legacy-lesson backfill landed, inert. New module
  `core/harness/backfill.py`: `nearest_profile(lesson_ocean, profiles)` (cosine
  gated by `SIMILARITY_FLOOR`, tie-broken by lowest id, None when no profile is
  close enough or no vector), `plan_backfill(rows, profiles)` (pure; never
  overwrites a row that already carries a profile id — idempotency), and
  `apply_backfill(..., dry_run=True)` (the default writes nothing; `--apply`
  writes a side-car). Accepts a sequence of profile ids (loaded via
  `load_profile`) or an explicit id→vector mapping; coerces `O70 C90 …` rows
  and 0..100 / 0..1 scale. A synthetic mixed library (already-profiled /
  unprofiled-near / unprofiled-far / no-vector) exercises assign /
  already-profiled / keep-free / no-vector; dry-run is byte-identical on disk;
  re-running on the applied set yields zero assignments. Apply refuses when no
  profileable rows exist (the deferral's precondition as an executable truth,
  not a silent no-op). CLI flags `--rows/--profiles/--library-root/--apply`.
  `ocean-profile-schema.md` §9 now records the backfill as "implemented but
  unrun" instead of "absent". `python3 -m core.agent` green at **26 `ok:`**;
  `backfill.demo()` is self-contained. No real library exists, so nothing was
  applied; the side-car convention keeps EXPERIENCE.md untouched. agent.py
  partial-staged (single hunk, user-owned).
- **2026-11-04 (P7-2)** — The TS archive is a mirror, no longer the verbatim
  pre-P6 text. `core/harness/personality.ts` now also ships `FACET_MAP`,
  `aggregateFacets` (half-up `_round`), the `FACET_CONDITIONS` table and
  `facetDirectives` (registry order, mid silence, dedupe, table-gap raises), the
  `facets` field on `PersonaConfig`, a facet carve in `validate()`, the
  registry-ordered `## Facets` data block and the `## Facet directives` section
  in `buildSystemPrompt`, plus a `FACET_RIDEALONG` full-coverage twin fixture.
  The header notes when and why the archive stopped being verbatim. `_ts_reference_check`
  extends the bun cross-check from ocean-only to ocean + facet: it parses the
  `--- Facet prompt ---` / `--- Facet derived ---` markers from the archive's
  demo guard and asserts byte-equality of both the compiled text and the nine
  derived params against Python's `compile_persona(FacetTwin)` / `derive(FacetTwin)`.
  The original ocean pin (compiled preset + derived params) is left untouched.
  `prompts.VERSION` moved `harness2-1` -> `harness2-2` (mirrored in the upstream
  `harness2/` copy). Contract updated in `ocean-profile-schema.md`: the
  "archive remains pre-P6 verbatim" line in §7 Aggregation, and the "TS mirror
  is P7-2" line in §7 Compiled shape, both now state the mirror landed.
  phase-plans gained the P7-2 record. Self-check stays at **25 `ok:` lines**
  (no new named pin; the bun cross-check line now says ocean + facet); bun
  green; ZCode porcelain empty. TS now also mirrors all P7-1 invariants; the
  P6 Aggregation ponytail about the TS are resolved, and §7 "Deferred" no
  longer owes a mirror.

- **2026-11-04 (P7-1)** — Facet grain got its payload: `FACET_CONDITIONS`
  (30 facets × the four extreme bands; `mid` emits nothing — the domain
  `DIRECTIVES` table owns moderate behavior) and `facet_directives()`, rendered
  by `compile_persona` as `## Facet directives` above the `## Facets` data
  block, only in the non-degraded branch; ocean grain renders no facet section.
  Invariants pinned: **registry-order render** (both facet sections in
  `FACET_MAP` order, keep-first on duplicates — JSON key order cannot leak;
  rebuilt-dict compiles are byte-identical), **loud gaps** (a missing table
  entry raises; bands are `bandOf`'s own edges, no facet thresholds), and a
  **loud harness ceiling** `FACET_DIRECTIVE_CAP = 3000` enforced in
  `validate()` against the rendered section (trim the table, never truncate;
  shipped worst case 2656 so P6's full-coverage-compiles contract cannot break
  silently; `# ponytail:` self-judged boundary vs a second system prompt).
  Zero plumbing: the compiled persona is the mission slot's only source, so
  directives ride every profile-backed mission automatically; PSY/ledger seams
  untouched and grain-blind. TS archive stays verbatim pre-P6; the bun
  cross-check keeps pinning ocean-grain only (mirror is P7-2). Spec:
  `ocean-profile-schema.md` §5 cap bullet, §7 "Compiled shape" rewritten, §8,
  §9 (FACET_CONDITIONS off the deferred list); phase-plans gained the P7
  section. `python3 -m core.agent` green at **25 `ok:` lines** (24 pins + the
  TS cross-check): demo gained a ten-pin facet-directive block, agent self-check
  21 proves the mission-slot seam (facet grain → 4 directive lines through
  `resolve_prompts`, `- trust (veryHigh):` needle; ocean grain → none).
  agent.py partial-staged as a single hunk (user-owned); ZCode porcelain empty.

- **2026-11-04 (P6)** — The plan's first deferred item landed: the 30-facet Big
  Five grain is a legal second profile shape (`FACET_MAP`, mutually exclusive
  with `ocean`, unknown names refused; values gated 0–100 since P4). The kernel
  stays untouched and ocean-grain only — `aggregate_facets` folds facet values
  into the loaded `ocean` at load (domain = `_round` mean of present facets,
  half-up like `Math.round`; missing domains fill to neutral 50). Full coverage
  (≥1 facet per domain) compiles through the normal path — bands, Voice/Honesty,
  threshold rules, PSY 3-D, ledger — plus a data-only `## Facets` block; partial
  coverage keeps the P4 authored degradation with `out_of_band()` naming the
  domains with no facet evidence. Pins: kernel purity (facet pairs `(s-1, s)`
  average to `s−0.5` and must land on a committed data-only ocean twin's own
  scores — identical OCEAN section + derived params; the twin is selected
  without spelling its id so the P4 zero-code scan stays clean), partial
  degradation, loud-refusal gates (mutual exclusion, unknown facet, neither
  grain), and self-check 20 riding `resolve_prompts` to 3-D
  (`("INV","DOM","PSY")`). Spec first: `ocean-profile-schema.md` §7 renamed
  "Facet vectors and the degradation contract"; phase-plans gained the P6
  section. `python3 -m core.agent` is green at **24 `ok:` lines** (23 pins +
  the TS cross-check); bun
  cross-check untouched; `personality.ts` verbatim; ZCode porcelain empty.
  Deliberately open (`# ponytail:`): `FACET_CONDITIONS` per-facet directive
  table, a TS mirror of aggregation, and no committed facet-grain profile.
  agent.py hunks partial-staged (user-owned); P4 check 17 reworked to a
  facet-only construction (mutual exclusion), assertions unchanged. The engine
  leg then proved the grain through the real stack offline (docs commit):
  zcode-substrate agg sweep as `zcode-zcode-agg1-Pfacetride` — row ok, ledger
  R=5/P=4/n_dims=3 exact, 4 markers, selection ok=True, runmark resume skip on
  re-invocation; and an ADE deploy+boot at an isolated leg root —
  `source=deployed:default`, facet-derived temperature 0.62, compiled c1 with
  `## Personality (OCEAN)` + `## Facets` (`fantasy 59 (openness)`). Ponytail
  pinned: boot.py reads the APRIL surface's c1/c2 names, so a ZCODE-surface
  deployment boots empty-c1 (out of scope; the ADE runtime host is APRIL —
  the note lives in the evidence file). Legacy-lesson backfill stays deferred —
  no real legacy library exists yet (one doc-sample row; no `.ade/library`),
  matching the deferral's own precondition. Evidence:
  `runs/zcode-zcode-agg1-Pfacetride/p6-facet-engine-leg.md`.

- **2026-11-03 (P5)** — The ADE landed: adopted trees deploy to
  `.ade/harnesses/<persona>/<domain>/` (git-ignored, `APRIL_ADE_ROOT` override) and
  run with a persona at `APRIL_PERSONA=<id>` boot. Specs first
  (`docs/spec/zcode-substrate.md`, `docs/spec/persona-dimension.md`), then
  `core/ade/{deploy,boot,views,__main__}.py`. Deployment validates on
  `APRIL_SURFACE` and refuses invalid sources (never sanitizes); a persona-free
  adopted tree is stamped (`## persona: compiled` in c1, `## persona: protocols` in
  c2) with the proposer's own sections untouched, fingerprinted via
  `runmark.fingerprint`, and indexed. A separate `ADE_PERSONA_CAP = 6000` ceiling
  governs deployments (the engine's `PERSONA_CLAUSE_CAP = 1600` stays with
  proposals; a deployed persona IS the runtime system prompt). `load_deployed`
  refuses bytes-changed-after-deploy trees (tamper → loud error, not fallback).
  Boot order: deployed tree → committed profile → loud failure; c1 becomes the
  system message head (exactly once), c2 the permission layer, and
  `derive().temperature` overrides sampling (`toRequest()` semantics). The
  permission layer reuses the post-selection machine gates verbatim via the new
  public seam `judge.public_action_hits/approved_marker/secret_hits`: unapproved
  public actions return a `[persona-guard]` tool result (never executed, marker
  re-entry instructed), secrets are refused, everything else attested. Local user
  facts join at boot only from git-ignored `.ade/etc/persona-user-context.md` and
  the TUI view reports a count, never the text. The ADE is agent-driveable both
  ways: plain callables and `python3 -m core.ade {deploy,boot,view}`; TUI widgets
  stay deferred behind the JSON contracts in `core/ade/views.py`. One downstream
  seam wrapped, not fixed: the boot's status emit crashes inside user-owned
  `core/events.py` (`StatusEvent` has no `event_type`) — swallowed at the seam
  with a `# ponytail:` note. Acceptance: self-check 19 runs deploy → tamper
  refusal → boot → guard → runtime → view offline; `python3 -m core.agent` is
  green at 19 checks and `git -C ZCode status --porcelain` is empty. The final
  gate is then proven LIVE the same day: an offline sweep adopted `agg1`
  (`ok=True fallback=False`), `python3 -m core.ade deploy` stamped + indexed it,
  and `APRIL_PERSONA=april` booted a real session from the deployed adopted tree
  (`source=deployed:default`, temp 0.69, 4 local facts) on the Free-plan model:
  a shell round-trip answered from real tool output, READ-ONLY git inspection
  passing the machine gates, an unapproved-push refusal and an API-key echo
  refusal per c2 protocols, and even a verbatim `git push origin HEAD`
  instruction declined with reasoning (evidence:
  `runs/april-april-agg1/demo-0001/p5-live-session.md`; the [persona-guard]
  block branch stays pinned offline in check 19 — the live models never proposed
  a public action, and all ten live tool calls passed gate evaluation without a
  false positive). GLM finding: `glm-5.3-flash` is served by the configured
  gateway but 402 on this account plan — wiring is config-only
  (`OPENAI_MODEL=glm-5.3-flash`), the spend decision stays with the user.
- **2026-11-03 (P4)** — The scale-out contract is proven by data: a third
  persona, `profiles/ridealong.json` (low N, high A, mid E), lands as one JSON
  with no registry and no harness code naming it; the offline pin scans
  `core/harness/**/*.py` for the id and fails if it appears. A 1-task sweep runs
  it at full arithmetic (`-Pridealong`, R=5=5k, P=4=4k, per-profile library
  root, four markers). `docs/spec/ocean-profile-schema.md` documents the format
  as a contract: field table, band edges, every derivation formula, validation
  rules, the 0.95 cosine gate with measured pairs, and the add-a-profile recipe.
  Out-of-band vectors (`facets`, the deferred 30-facet expansion) load and run
  but degrade to authored c1 guidance: `personality.out_of_band` names the
  ceiling and carries the `# ponytail:` note, `compile_persona` withholds the
  OCEAN/Voice/Honesty/threshold blocks, and `psy_mechanism` returns `""` so the
  ledger stays 2-D (R=4k/P=3k). Load now refuses unknown top-level, `ocean` and
  `relationship` keys, so a profile typo cannot silently compile nothing.
  `python3 -m core.agent` runs 22 `ok:` checks, including the ridealong sweep and
  the facet-degradation path through `resolve_prompts`.
- **2026-11-02 (P3)** — PSY landed as a third learnable dimension, active only
  while a profile is: `resolve_prompts` fills `psy_levers` from
  `personality.psy_mechanism(profile)` (derived params, never raw traits),
  `prompts.active_dims()` then returns `(INV, DOM, PSY)`, `propose(...)` rejects
  PSY without levers, and `recursion` schedules `k` more candidates with the
  phase width still `min(dims, PHASE_WIDTH)`. `Ledger` gained validated
  `n_dims` (2 or 3), so the same exactness assert now proves R=5k/P=4k on
  profile runs and 4k/3k elsewhere; `ModeResult`/`TaskResult` summaries carry
  `n_dims`/`persona`. The new `_PSY_TEMPLATE`/`_TTS_PSY` own expression only
  (tone, pacing, delegation, verification temperament) and disclaim the INV/DOM
  lanes; aggregate/compose name the full 3k candidate set. Post-selection
  `judge.trait_checklist` runs exactly two deterministic gates (unapproved
  public action, with an `user approved` marker; secrets hygiene) and records
  every protocol/extra/threshold rule as `attested` — the blind selector still
  sees nothing. `selection.edit_attribution` splits
  `ComponentRegistry.diff` by stage dimension; an all-PSY lesson is
  `transferable: false`, and `_annotate_edits` writes the `dimensions:` row.
  `Library(profile=...)` ranks by OCEAN cosine and withholds below
  `SIMILARITY_FLOOR=0.95` non-transferable lessons (april↔user 0.9667 passes,
  april↔verifier 0.9111 gated), including from the browse index. `ns()` gained
  `-P<persona>` and `run_sweep(profiles=...)` fans out with per-profile adapter
  specs, namespaces and library roots. Two seams fixed on the way: the evidence
  tree now materialises inline `task_files()` fixture mappings, and a neutral
  selector fallback now counts as a selection (`ok=True`, `fallback=True`)
  instead of failing the mode. `python3 -m core.agent` runs 18 checks including
  a k=2 persona run (R=10=5k, P=8=4k, eight markers) and a two-profile sweep.
- **2026-11-01 (P2)** — The personality kernel landed as a constraint layer:
  `core/harness/personality.ts` archives the reference implementation (Appendix A
  verbatim minus the two redaction blocks moved to `.ade/etc/persona-user-context.md`);
  `personality.py` is the faith-port with JS `Math.round` fidelity (`_round`,
  APRIL proactivity 66.5→67) and a live **byte-equal bun cross-check** of the
  compiled APRIL preset + all nine derived params, running inside
  `personality.demo()`; `profiles/{april,user,verifier}.json` landed with no
  `userContext` (loader rejects it; personal facts stay in the git-ignored
  `.ade/etc/persona-user-context.md`); `HarnessSpec.profile` +
  `HarnessPrompts.{persona_guidance,psy_levers}` slots ride the bad-word filter,
  `resolve_prompts` compiles the active profile into `persona_guidance`, and the
  five proposal missions (`propose`/`aggregate`/`compose_round`, both packs) gain
  `_persona_part` blocks — selector/experience stay persona-free;
  `tree.validate` caps tagged `## persona:` sections in always-on files at
  `PERSONA_CLAUSE_CAP=1600` through the existing `STAGE_ATTEMPTS=2` retry path;
  the ledger went dims-parameterized (`(2+n_dims)·k` / `(n_dims+1)·k`, defaults
  keep 4k/3k); lessons gained `{profile_id, ocean}` provenance
  (`_distil` → `Library.add` → index → root `EXPERIENCE.md` Persona column);
  `ZcodeAdapter(profile=...)` rides `spec.profile`. `python3 -m core.agent` now
  runs 15 checks including the four P2 ones.
- **2026-10-31 (P1)** — The zcode substrate landed end to end, offline-first:
  `tree.py` grew per-surface layouts (zcode sub-agents at
  `.zcode/agents/<name>.md`, plugin manifests at
  `.zcode/plugins/<name>/.zcode-plugin/plugin.json`, a `MEMORY.md` seed file with
  its own cap, nested run-config hooks over the seven-event v2 list with
  `command`/`process` types) with the legacy layout byte-identical by default;
  the P1-1.3 gate matrix (skill frontmatter, per-skill trigger line, hooks
  enablement/events/types, plugin manifest fields + orphan content, snake_case
  script compilation) runs in `tree.validate`; the process-boundary adapter
  (`zcode_adapter.py`) runs ZCode's documented batch recipe with per-sample
  isolation, fresh-sample retries, and the forced-deliverable contract; three
  authored mini-tasks (`benchtasks/`) feed the sweep with no grader vocabulary;
  `schedule.run_sweep_zcode` is the first full sweep. Offline everything runs
  against the stub (`APRIL_ZCODE_BIN` → `core/harness/zcode_cli_stub.py`);
  `python3 -m core.agent` now proves the zcode end to end (stub rollout, memory
  seeding into the CLI's isolated store, `run_task` offline with exact ledger
  identity R=4k/P=3k, and `git -C ZCode status --porcelain` empty).
- **2026-10-31 (P0)** — Harness² engine repaired and put under version control
  honestly: `core/harness/config.py` grew the missing `BUN`/`OPENCODE_DIR` surface
  and the broken `opencode` default was replaced by `claude_cli` (the opencode
  channel now fails closed as `ProposerError` until a checkout exists); rollouts
  mark completion (`.adapt_complete`) only when `execute()` returns a real run dir;
  the degenerate recursion paths were deleted so `run_task` drives the real
  `recursion.run_mode` through `schedule.run_task` — one path to `execute()`,
  k shared bases, exact ledger identity (R=4k, P=3k) even when proposal stages
  error-fallback; `core/agent.py` got its real substrate (`core/substrate/april.py`
  SurfaceSpec), its base harness (`core/harness/basetree/april/`, three always-on
  files) and the `APRIL_EXECUTOR=dry` structural executor; `prompts.py` grew the
  `april` prompt-surface table; offline self-check lives in
  `python3 -m core.agent`. Engine directories are now tracked in git.
- **2026-10-31** — Doc set consolidated: the 22 superseded implementation/architecture
  documents (~15k lines, tracked in history) were removed and replaced by
  `docs/spec/phase-plans.md` — the standing detailed plan for phases P0–P5 (repair +
  consolidate, ZCode substrate, personality kernel, PSY dimension, scale-out, ADE
  deployment + runtime persona). New doc rule: specs go spec-first into `docs/spec/`;
  new standing principle: the ADE must itself be an APRIL agent.
- **2026-09-04** — Rescoped the body from *fabrication* to *the networked world*.
  The ESP32 is reframed as a programmable wifi bridge, not a telemetry board (the
  telemetry read was its first test flash); devices become a domain table —
  vision/surveillance, sensing/medical, home/appliances, robotics/custom MCU,
  network/security, industrial, fabrication — with fabrication one domain among
  many rather than the headline. The software pillar broadens from design tools to
  vision and network/security tooling too. Safety grows from one axis to three:
  per-device physical risk, a hard network-authorization line, and data-sensitivity
  on reads. The proof points become one small win per mode (see / act / network /
  build) instead of a single fabrication flagship.
- **2026-09-03** — Redesigned around the real idea: an assistant with a body.
  Devices are limbs, not integrations; the three pillars (machine, software,
  devices) replace a single shell tool; APRIL becomes the core agent that plans
  and dispatches to skill-carrying subagents; safety splits into a light policy
  for software and a per-device risk policy for physical machines; projects and
  the two-track roadmap added. The separate upstream **MoE router** was dropped —
  model routing belongs inside APRIL, and starts with reasoning only.
- **2026-09-03** — Reset to scope. The three empty source files were dropped and
  this document stripped back to what APRIL is and should do; implementation
  detail is decided per file, as each is written.
- **2026-09-02** — Collapsed to a single generation. The original macOS app
  (AppKit window, voice, vision) was removed outright along with `parked/`, and
  the five documentation files (`README`, `AGENTS`, `docs/CONTEXT`,
  `docs/ARCHITECTURE`, `docs/ROADMAP`) were merged into this one file.

---

## What's NOT here

The Harness² engine is real and runs — offline on two substrates today (the
`april` dry executor and the `zcode` adapter behind the process-boundary stub),
on the real zcode CLI once `APRIL_ZCODE_BIN` points at a build — but its money
paths all fail closed without a model. Still not
built: real zcode execution against a SEA build, physical limbs beyond the ESP
telemetry prototype, the device registry, runtime subagents, durable projects,
persistence beyond the memory database, and the interface beyond a terminal.

The app, voice, and vision are **deferred, not abandoned** — they return once
there is something underneath them worth interfacing with.
