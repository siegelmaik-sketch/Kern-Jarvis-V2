# Kern-Jarvis V2

A self-learning, persistent AI assistant — runs locally on your system and builds its own tools on demand.

## Concept

Jarvis starts with a fixed core (who he is, how he thinks) and an empty toolbox. When given a task he has no tool for, he builds it himself — tests it, saves it persistently, and uses it directly from then on without any LLM call.

```
Task → Tool available? → execute
             ↓ no
       Build tool → test → save → execute
```

## Features

- **Self-Tool-Builder** — Jarvis writes Python tools on demand
- **Persistent Memory** — 3-tier system with quality gate: messages, semantic archives, long-term facts
- **Implicit Memory** — automatically extracts commitments, decisions and TODOs from conversations
- **LLM-agnostic** — Anthropic, OpenAI or OpenRouter (one API key is enough)
- **Semantic Search** — embedding-based retrieval over facts and archived conversations
- **Eternal loop** — no restart needed when new tools are added, dynamic manifest
- **SQLite** — no external database required
- **Onboarding** — guided setup on first launch

## Installation

```bash
git clone https://github.com/siegelmaik-sketch/Kern-Jarvis-V2.git
cd Kern-Jarvis-V2
pip install -r requirements.txt
python3 -m kern
```

On first launch, Jarvis guides you through setup (name, language, LLM provider, API key, model selection).

For development:

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

## Supported Providers

| Provider | Model Examples |
|---|---|
| Anthropic | claude-opus-4-6, claude-sonnet-4-6 |
| OpenAI | gpt-4o, gpt-4o-mini |
| OpenRouter | All available models |

Embeddings run via OpenRouter regardless of your main provider.

## Commands

```
/hilfe    Show help
/tools    Show all registered tools
/memory   Show memory contents
/search   Semantic search over memory (e.g. /search Bitcoin)
/config   Show or change configuration (e.g. /config set llm_model ...)
/reset    Clear message history (facts are preserved)
/exit     Quit
```

## Structure

```
kern/
  __main__.py        Entry point (python -m kern)
  brain.py           LLM adapter with client caching
  memory.py          3-tier memory: messages, archives, facts
  implicit_memory.py Automatic extraction of actionable info
  tools.py           Tool registry + execution with path validation
  tool_builder.py    Self-build loop + command parser
  loop.py            Core loop + slash commands
  onboarding.py      First-run setup wizard
  db.py              SQLite with connection context manager
  exceptions.py      Exception hierarchy
  schema.sql         Database schema
prompts/kern.md      Static core prompt
tools/               Self-built tools (gitignored)
data/                SQLite database (gitignored)
tests/               171 tests
```

## Configuration

All configuration is stored in SQLite (not environment variables). The onboarding wizard handles initial setup. After that, use `/config` to change settings at runtime:

```
/config                          Show all settings
/config set llm_model <id>       Change main model
/config set memory_llm_model <id> Change memory model (should be cheap)
/config set llm_api_key <key>    Change API key
```

## Philosophy

Jarvis is not a wrapper — it's a system that grows with you. Every tool it builds, every correction you give, every context you share is retained permanently. Tokens are saved by offloading repeatable tasks to tools instead of calling the LLM every time.

## License

MIT
