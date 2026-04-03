# Kern-Jarvis V2

Ein selbstlernender, persistenter KI-Assistent — läuft lokal auf deinem System, baut sich seine Tools selbst.

## Konzept

Jarvis startet mit einem festen Kern (wer er ist, wie er denkt) und einem leeren Werkzeugkasten. Wenn er eine Aufgabe bekommt für die er kein Tool hat, baut er es sich selbst — testet es, speichert es persistent und nutzt es ab sofort direkt ohne LLM-Aufruf.

```
Aufgabe → Tool vorhanden? → ausführen
                ↓ nein
          Tool bauen → testen → speichern → ausführen
```

## Features

- **Self-Tool-Builder** — Jarvis schreibt Python-Tools bei Bedarf selbst
- **Persistentes Memory** — erinnert sich an Nutzer, Feedback, Projekte, Referenzen
- **LLM-agnostisch** — Anthropic, OpenAI oder OpenRouter (ein API Key reicht)
- **Ewiger Loop** — kein Neustart bei neuen Tools, dynamisches Manifest
- **SQLite** — keine externe Datenbank nötig
- **Onboarding** — geführte Ersteinrichtung beim ersten Start

## Installation

```bash
git clone https://github.com/siegelmaik-sketch/Kern-Jarvis-V2.git
cd Kern-Jarvis-V2
pip install -r requirements.txt
python3 jarvis.py
```

Beim ersten Start führt Jarvis dich durch die Einrichtung (Name, Sprache, LLM-Provider, API Key).

## Unterstützte Provider

| Provider | Modell-Beispiele |
|---|---|
| Anthropic | claude-opus-4-6, claude-sonnet-4-6 |
| OpenAI | gpt-4o, gpt-4o-mini |
| OpenRouter | Alle verfügbaren Modelle |

## Befehle im Chat

```
/tools    Zeigt alle registrierten Tools
/memory   Zeigt den Memory-Inhalt
/reset    Neue Session (Memory bleibt erhalten)
/hilfe    Hilfe anzeigen
/exit     Beenden
```

## Struktur

```
jarvis.py          Einstiegspunkt
kern/
  brain.py         LLM-Adapter
  memory.py        Memory-System
  tools.py         Tool-Registry
  tool_builder.py  Self-Build-Loop
  loop.py          Kern-Loop
  onboarding.py    Ersteinrichtung
  db.py            SQLite
prompts/kern.md    Statischer Kern-Prompt
tools/             Selbstgebaute Tools (gitignored)
data/              SQLite Datenbank (gitignored)
```

## Philosophie

Jarvis ist kein Wrapper — er ist ein System das mit dir wächst. Jedes Tool das er baut, jede Korrektur die du gibst, jeder Kontext den du teilst bleibt dauerhaft erhalten. Token werden gespart indem wiederholbare Aufgaben als Tool ausgelagert werden statt jedes Mal das LLM zu bemühen.
