# Plans

Forward-looking plans for APRIL, kept separate from the two other plan layers:

| Layer | Location | Purpose |
|---|---|---|
| Master plan | `~/.opencode/plan` | Authoritative phase plan and execution record (local, not versioned) |
| Standing phase plans | [`../spec/phase-plans.md`](../spec/phase-plans.md) | Repo-side record of P0–P7: what each phase changes and how it is verified |
| **Current-state plans** | **this folder** | Plans written from the current codebase state, for work that comes after P7 |

## Current state (2026-11-07)

- P0–P7 complete; 31 offline self-check pins, 55 pytest, ZCode porcelain clean.
- Revision report: [`../Reports/revision-1-progress.md`](../Reports/revision-1-progress.md).
- Known gaps for the next plan: ADE REST transport (`/api/ade/*`), adapter
  registry, MCP runtime client/server, tool registry (browser as a tool),
  session/history endpoints + API auth, packaging (`pyproject` package discovery).

Add each new plan as its own dated file here; link it in this index.
