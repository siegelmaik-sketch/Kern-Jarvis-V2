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
- **Persistent Memory** — remembers user preferences, feedback, projects and references
- **LLM-agnostic** — Anthropic, OpenAI or OpenRouter (one API key is enough)
- **Eternal loop** — no restart needed when new tools are added, dynamic manifest
- **SQLite** — no external database required
- **Onboarding** — guided setup on first launch

## Installation

```bash
git clone https://github.com/siegelmaik-sketch/Kern-Jarvis-V2.git
cd Kern-Jarvis-V2
pip install -r requirements.txt
python3 jarvis.py
```

On first launch, Jarvis guides you through setup (name, language, LLM provider, API key).

## Supported Providers

| Provider | Model Examples |
|---|---|
| Anthropic | claude-opus-4-6, claude-sonnet-4-6 |
| OpenAI | gpt-4o, gpt-4o-mini |
| OpenRouter | All available models |

## Commands

```
/tools    Show all registered tools
/memory   Show memory contents
/reset    New session (memory is preserved)
/hilfe    Show help
/exit     Quit
```

## Structure

```
jarvis.py          Entry point
kern/
  brain.py         LLM adapter
  memory.py        Memory system
  tools.py         Tool registry
  tool_builder.py  Self-build loop
  loop.py          Core loop
  onboarding.py    First-run setup
  db.py            SQLite
prompts/kern.md    Static core prompt
tools/             Self-built tools (gitignored)
data/              SQLite database (gitignored)
```

## Philosophy

Jarvis is not a wrapper — it's a system that grows with you. Every tool it builds, every correction you give, every context you share is retained permanently. Tokens are saved by offloading repeatable tasks to tools instead of calling the LLM every time.
