# APRIL Plan — Unified Feature Adapter Runtime

## Objective
Unify all APRIL features (identity, memory, tools, skills, subagents, MCP, tasks)
behind one phased `FeatureAdapter` lifecycle so the LLM wrapper (`AgentRuntime`) injects
fresh state **before** every API call and runs post-processing **after** each turn.

## Unified contract

```python
class FeatureAdapter(Protocol):
    name: str

    def pre_call(self, agent, user_input) -> str:
        """Context text inserted before the LLM call ("" if none)."""

    def tool_schemas(self, agent) -> list[dict]:
        """OpenAI tool schemas this feature contributes ([] if none)."""

    def dispatch(self, tool_name, args, agent) -> str | None:
        """Execute a tool owned by this feature, or None if not owned."""

    def post_call(self, agent, user_input, assistant_response) -> None:
        """Post-processing after the model responds."""
```

## Runtime pipeline

1. PRE-CALL
   - for each feature: `ctx += feature.pre_call(...)`
   - collect `tools = feature.tool_schemas(...)`
   - build request:
     ```
     [system: identity]
     [system: runtime context]
     + session conversation
     ```
2. LLM call with merged `tools`.
3. If `tool_calls`: dispatch through features (first ownership wins), append
   tool results, rebuild request, repeat.
4. POST-CALL
   - for each feature: `feature.post_call(...)`.

## Feature mapping

| Feature | pre_call | tool_schemas | dispatch | post_call |
|---|---|---|---|---|
| Identity | APRIL/persona prompt | [] | None | None |
| Memory | retrieve top-k | [] | None | extract + store |
| Tool registry (Shell/Browser/MCP) | summarize names/desc | their schemas | execute owned tool | None |
| Skills | catalog (name/scope/description) | `load_skill`, `list_external_skills`, gated CRUD/import | load/CRUD/import | None |
| Subagents | capability note (parent only) | `run_subagent`, `list_subagents` (depth 1) | spawn + wait | None |
| MCP | server status + failures | discovered `tools/list` schemas | MCP `tools/call` | connection lifecycle |
| Task context | pinned task text | [] | None | None |

## Migration steps

1. Add `FeatureAdapter` protocol and default no-op adapter in `core/feature_adapter.py`.
2. Add `FeatureRuntime` registry to `AgentRuntime` (`self.features: list[FeatureAdapter]`).
3. Implement `_build_request_messages(user_input)` (ephemeral identity + context + conversation).
4. Migrate `ShellTool` → `ShellFeatureAdapter` (schemas + dispatch) as proof of concept.
5. Migrate memory retrieve/extract into `MemoryFeatureAdapter`.
6. Move persona identity into `IdentityFeatureAdapter`.
7. Wrap skills/subagent/MCP stubs as no-op adapters; fill later.
8. Replace `messages=self.session.context` with `_build_request_messages(...)` in `run()` and `run_streaming()`.
9. Add `post_call` invocation after final response.
10. Remove direct `session.context` system-row mutation; keep conversation rows only.

### Phase 1 — Refactor only (STOP after this)
Do steps 1–10 above, then **stop**. Do not add or run tests yet. Ask the user to switch models before continuing.

### Phase 2 — Tests / iteration (only after model switch)
11. Add tests:
    - "What's your name?" → contains "APRIL".
    - Fake feature text reaches mocked LLM request messages.
    - Tool dispatch ownership and post-call hooks fire.
12. Run/repair loop until green.

### Phase 3 — Fill skills / subagents / MCP (implemented)

- **Skills** (`core/feature_adapter.py`): catalog in `pre_call`; `load_skill`,
  `list_external_skills`; gated `create_skill` / `update_skill` / `delete_skill` /
  `import_skill` (workspace or `~/.agents/skills`, `confirm=true` required).
  Imports copy directories from known local-agent roots (Claude, Codex, OpenCode,
  OpenClaw, and similar). `APRIL_SKILL_WRITE=0` hides write tools.
- **Subagents**: blocking `run_subagent` (default timeout 300s) and
  `list_subagents`; child runtimes get `_subagent_depth >= 1` and no subagent or
  MCP tools.
- **MCP** (`core/mcp_client.py`): stdio + Streamable HTTP JSON-RPC client,
  eager connect at runtime construction, namespaced `mcp__<server>__<tool>`
  tools, per-server/per-tool status, `APRIL_MCP_DISABLE=1` kill switch,
  `APRIL_MCP_REGISTRY` path override.
- **Surfaces**: `GET /api/mcp/status`, `GET /api/tools`; inspector "tools" tab
  renders MCP server/tool status and available tool schemas.
- **Lifecycle**: `AgentRuntime.close()` closes MCP connections and subagent
  workers; adapter `close()` is terminal (a later call does not reconnect).

## Backward compatibility

- Keep `AgentRuntime(config, tools=...)` signature working; map `tools` into the registry.
- Preserve `session.context` and `get_context_summary()` shapes.
- Provider failures never block an LLM call (swallow + log).

## Suggested model for implementation

- **Primary implementation (multi-file refactor):** Beam 1.0 501B — best for coordinated changes across modules while preserving invariants.
- **Fast iteration/tests:** DeepSeek v4.1 Flash — cheaper/faster for test fix loops.
- **Avoid GLM 5.3 Flash here:** account/plan returned 402 in this environment; not viable unless billing changes.
- Recommended workflow: implement with Beam 1.0 501B, then run/repair tests with DeepSeek v4.1 Flash.
