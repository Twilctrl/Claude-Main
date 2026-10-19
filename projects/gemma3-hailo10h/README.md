# Gemma 3 270M on the Hailo-10H

Compiling `google/gemma-3-270m-it` into a HEF that runs on a Raspberry Pi AI HAT+ 2
(Hailo-10H) and is served by hailo-ollama. Hailo doesn't officially support compiling
custom LLMs. This project builds on the community compiler
[l-nmch/hailo-10h-llm-compiler](https://github.com/l-nmch/hailo-10h-llm-compiler) (MIT),
which uses the Dataflow Compiler's LLM flow.

> **Status: Phase 0 of 7 done.** Nothing has been compiled yet. Upstream only gets
> coherent output on the chip from tiny TinyStories-style models; every
> general-purpose model it tried, Gemma 3 270M included, fails today. See
> [docs/phase0-community-repo.md](docs/phase0-community-repo.md).

## Phases

| # | Phase | Status |
|---|---|---|
| 0 | Read upstream; summarize the pipeline, runtime contract and export fixes; list installs | done |
| 1 | Reproduce upstream on TinyStories-25M; check prefill against float32 | waiting on setup decisions |
| 2 | Audit Gemma 3's architecture against the DFC (`docs/gemma3-audit.md`) | – |
| 3 | ONNX export at a fixed context | – |
| 4 | Embedding table and tokenizer | – |
| 5 | Calibrate, quantize (INT8, then an INT4 mix), compile | – |
| 6 | Validate prefill and multi-token generation | – |
| 7 | Register in hailo-ollama on the Pi; benchmark | – |

[NOTES.md](NOTES.md) is the running log: every attempt, its numbers, and the next
hypothesis. Start there.

## Layout

```
NOTES.md         lab notebook (read first)
versions.env     every pinned version: DFC, HailoRT, torch, transformers, upstream commits, model revisions
docs/            findings, one page per topic
scripts/         reproducible steps, numbered by phase
work/            git-ignored: upstream checkouts, the DFC wheel, pipeline artifacts
```

## Getting started

```bash
scripts/00_fetch_upstream.sh                   # pinned upstream sources into work/upstream/
scripts/00_check_env.sh --wheel-dir work/dfc   # read-only readiness report for the compile host
```

What to install and download first: [docs/setup-checklist.md](docs/setup-checklist.md).

The Dataflow Compiler is proprietary and not redistributable. Download it yourself
from the Hailo Developer Zone. It and every compiled artifact (`.whl`, `.har`, `.hef`)
are git-ignored here.
