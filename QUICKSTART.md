# APRIL Quick Start Guide

## Overview

APRIL is an assistant with a body - it senses the world, acts on it, and works the network itself.

**Current State:** Harness² engine + ADE persona runtime (P0–P7 complete), with a
web/API surface, a CLI REPL, and the Textual TUI (available, parked).

---

## Quick Start

### 1. Setup

```bash
# Create .env file with your API configuration
cp .env.example .env

# Edit .env with your API key and model
# OPENAI_API_KEY=your_key
# OPENAI_API_BASE_URL=http://localhost:11434/v1
# OPENAI_MODEL=mistral
```

### 2. Run

```bash
# Web UI + API (one command; starts both services)
python scripts/dev.py
# then open the Vite URL it prints

# or: terminal UI
python main.py

# or: plain CLI REPL
python cli/main.py
```

### 3. What You'll See

**Web UI (`web/`):**
- Chat view streaming from `POST /api/chat`
- Live events from `GET /api/events` (SSE)
- Module shell: Chat, Skills, MCP, Hooks, Terminal, Browser, Settings
  (management screens are still placeholders)

**TUI (terminal):**
- Message list - Chat with APRIL
- Status bar - Shows agent state
- Stats panel - Memory and session stats
- Log panel - Real-time backend logs

**Backend Logger (Hidden by default):**
- All system events logged to `logs/april_backend.log`
- Memory updates, tool calls, LLM requests

---

## Usage

### Launch TUI

```bash
python main.py
```

### Launch with Debug Logging

```bash
python main.py --log-level DEBUG
```

### Launch with Log Panel Visible

```bash
python main.py --show-logs
```

### Launch with Custom Model

```bash
python main.py --model qwen2.5-coder:7b
```

### Launch Without Agent (Testing)

```bash
python main.py --no-agent
```

---

## View Backend Logs

```bash
# View latest logs
cat logs/april_backend.log

# Follow logs in real-time
tail -f logs/april_backend.log
```

---

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Terminal entry point (Textual TUI) |
| `cli/main.py` | Plain CLI REPL |
| `scripts/dev.py` | Starts the API + web frontend together |
| `core/api.py` | FastAPI/SSE backend over the agent runtime |
| `core/agent.py` | Agent runtime and model integration |
| `core/harness/` | Harness² engine (recursion, ledger, selection, library) |
| `core/ade/` | ADE deploy/boot/guard and view contracts |
| `core/textual_tui.py` | Terminal UI (parked) |
| `web/` | React/Vite frontend |
| `docs/spec/phase-plans.md` | P0–P7 standing phase plans |
| `docs/Reports/` | Revision reports |

---

## Architecture

```
Web UI (web/) ── EventSource/REST ──→ core/api.py ──→ AgentRuntime ──→ LLM / Tools
                                              │
TUI (Textual) ←── EventBus / IPC ─────────────┘
                                              │
                                     Memory / Subagents
                                              │
                                     Harness² engine (offline dry by default)
```

### Components

1. **TUI Layer** - User interface
   - Message exchange
   - Status display
   - Real-time logs

2. **Backend Logger** - System monitoring
   - Memory updates
   - Tool executions
   - LLM API calls

3. **Agent Runtime** - Core intelligence
   - LLM integration
   - Tool orchestration
   - Memory management

4. **Database** - Persistent storage
   - Message history
   - Extracted memories
   - Session state

---

## Event Flow

```
1. User types: "List files"
   ↓
2. TUI calls: agent.run()
   ↓
3. Backend logs: [USER] List files
   ↓
4. Backend logs: [LLM] Request to model
   ↓
5. LLM processes
   ↓
6. Backend logs: [TOOL] Calling: shell
   ↓
7. Tool executes
   ↓
8. Backend logs: [TOOL] Result: ...
   ↓
9. Backend logs: [LLM] Response
   ↓
10. Backend logs: [AGENT] Response text
   ↓
11. TUI displays: Response
```

---

## Log Tags

- `[USER]` - User input
- `[AGENT]` - Agent responses
- `[LLM]` - LLM API calls
- `[TOOL]` - Tool executions
- `[MEMORY]` - Memory updates
- `[SESSION]` - Session events
- `[ERROR]` - Errors

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+L` | Toggle log panel |
| `Ctrl+S` | Show stats |
| `Ctrl+Q` | Quit |
| `Ctrl+C` | Clear input |

---

## Database Locations

```
data/
├── april_memory.db      # SQLite database
└── codes/               # Code snippets (future)
```

---

## Development

### Code Generation (Future)

Qwen3 Coder will write code to the `data/codes/` directory based on your requests.

### Memory System

APRIL automatically extracts and stores memories from your conversations.

### Tool Framework

Extensible tool system for integrating external APIs and services.

---

## Support

- **Documentation:** `docs/`
- **Issues:** Open an issue on GitHub
- **Questions:** Check `docs/spec/phase-plans.md` and `docs/Reports/`

---

## Next Steps

1. **Customize**: Modify `.env` with your API settings
2. **Explore**: Use the TUI to interact with APRIL
3. **Monitor**: Check `logs/april_backend.log` for details
4. **Extend**: Add your own tools and models

---

## License

MIT License - See `LICENSE` for details.
