# Hive

Run several local AI models at once and let them work together on a shared
project. Fully offline: Hive talks only to model servers you run yourself
(Ollama, hailo-ollama, llama.cpp, LM Studio, vLLM), on this machine or on your
LAN.

> **Status: in development.** See [docs/DESIGN.md](docs/DESIGN.md) for the
> design. This README grows into the full guide as milestones land.

## Quick start

```
cd projects/hive
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
hive init               # finds Ollama on localhost and picks a model
hive backends --check   # is everything up?
hive chat               # talk to one model
hive chat -b mock       # or to the built-in mock, no model needed
```
