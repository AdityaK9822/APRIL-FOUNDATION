# APRIL

An assistant with a body — the body is the networked physical world.

APRIL is a Python agent runtime with four seams:

- `april/llm/` — one OpenAI-compatible model backend.
- `april/learning/` — the Harness² recursive loop (propose/probe/compose, blind selection,
  lessons) with a native proposer.
- `april/tools/` — MCP, skills, hooks, plugins, subagents, builtins.
- `april/persona/`, `april/memory/`, `april/body/` — OCEAN persona kernel, one memory store,
  ESP32 telemetry.

`april/runtime/agent.py` is the composer; `april/app.py` is the only composition root.

## Run

```bash
cp .env.example .env              # point it at an OpenAI-compatible endpoint
python3 -m april                  # offline self-check: no model, no network, no device
python3 -m april.cli.main         # Textual TUI
python3 -m april.cli.main --api   # FastAPI on 127.0.0.1:8000
```

Install the console script:

```bash
pip install -e .
april
```

## Configuration

`.env` / `.env.example`: `OPENAI_API_KEY`, `OPENAI_API_BASE_URL`, `OPENAI_MODEL`,
`APRIL_PERSONA`, `APRIL_MEMORY_DB`, `APRIL_WORKSPACE`, `APRIL_LEARN_TARGETS`,
`APRIL_ESP32_DEVICES`, `APRIL_MCP_REGISTRY`, `APRIL_TRUST_WORKSPACE_HOOKS`.

## Layout

```
april/
├── app.py        # composition root
├── runtime/      # the composer (turn sequence)
├── llm/          # model seam
├── learning/     # recursive engine, targets, native proposer
├── persona/      # OCEAN kernel, profiles, guards
├── memory/       # one store, extraction, patterns
├── tools/        # MCP, skills, hooks, plugins, subagents
├── events/       # one bus and logger
├── body/         # ESP32 telemetry
├── api/          # FastAPI front door
├── cli/          # Textual TUI
└── config/       # settings
```

Reference clones live at `docs/Ref/ZCode` and `docs/Ref/harness2` (git-ignored).

## Tests

```bash
python3 -m april          # offline self-check
.venv/bin/pytest tests -q # API + TUI front-door tests
```

## License

See `LICENSE`.
