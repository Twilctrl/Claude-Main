# Setup checklist

What each machine needs, and the decisions to make before Phase 1. Exact versions are
in [`../versions.env`](../versions.env). Nothing on this list gets installed for you
without asking first.

## Decisions needed before Phase 1

1. **Where the compiler runs.** The Dataflow Compiler (DFC) wheel has to be on the
   machine that runs Phases 1–6, and that machine must reach the Pi over SSH for the
   on-device checks. Today's Claude session runs in an Anthropic cloud container,
   not on your machine. That container has 4 vCPUs, 15 GB RAM, no GPU, about 30 GB
   free disk, a Docker client with no running daemon, and no route to your Pi. Three
   ways to proceed:
   - **(recommended)** Run Claude Code on your x86 Linux compile machine, in this repo.
     It has the wheel, the RAM, and SSH to the Pi.
   - Keep using cloud sessions to write code and docs, and run the scripts yourself,
     pasting results back. It works, but every iteration costs a round trip.
   - Use a cloud container for the DFC. You would have to supply the wheel yourself,
     15 GB RAM is probably too little for Gemma quantization, and there is still no
     route to the Pi.
2. **Toolchain version: 5.3.0 or 5.4.0.** Upstream is validated only on DFC 5.3.0 with
   HailoRT 5.3.0. Hailo's current release is 5.4.0. The genai pre-processing is
   identical in both, but hailo-ollama's manifest format changed, and the DFC's LLM
   flow is young. **Recommendation:** 5.3.0 everywhere for Phase 1, so any failure is
   ours rather than a version change. Move to 5.4.0 later as its own experiment.
3. **Looking at the Pi.** I'd like to run read-only checks:
   `hailortcli fw-control identify`, `dpkg -l | grep -i hailo`, `uname -a`, the Python
   version, and whether `hailo_platform.genai` imports. That needs your OK and an SSH
   route. The Raspberry Pi apt repo ships `hailo-h10-all` **5.1.1**. A HEF from DFC
   5.3.0 probably won't load on that until the Pi has the matching 5.3.0 suite.

## Compile machine (x86-64 Linux)

| Item | Version | Where from | Notes |
|---|---|---|---|
| Hailo Dataflow Compiler wheel | 5.3.0, `hailo_dataflow_compiler-5.3.0-py3-none-linux_x86_64.whl` | [Hailo Developer Zone](https://hailo.ai/developer-zone/) (free account, licence acceptance) | Proprietary. Put it in `work/dfc/` (git-ignored). Never committed or mirrored |
| Docker Engine | any recent | your distro or docker.com | System package: I'll ask before installing anything |
| NVIDIA GPU + NVIDIA Container Toolkit | optional, Turing or newer | NVIDIA | Speeds up quantization only. AMD works through upstream's ROCm image. CPU-only works but is slow |
| RAM | 32 GB minimum, 64 GB recommended | – | Upstream: quantizing a 28-layer model needed more than 56 GB. Gemma has 18 layers, but runs at a longer context |
| Free disk | 100 GB or more | – | The DFC image is ~15 GB. Upstream's 0.5B quantized HAR was 13.7 GB, and each step writes a new HAR |
| Hugging Face account and token | – | huggingface.co | `google/gemma-3-270m-it` is gated: accept the Gemma terms on its page, then create a read token |

Python stack, installed inside the container (pinned in `versions.env`): Python 3.10,
torch 2.6.0, transformers 4.57.6 (to be confirmed in Phase 2), numpy 2.1.3, scipy
1.13.1, onnxruntime 1.20.1. The rest is frozen with `pip freeze` after the first
successful build.

Downloads, in order:

| Download | Size | When |
|---|---|---|
| DFC wheel | (from Hailo) | before Phase 1, by you |
| `ubuntu:22.04` image, apt packages, Python packages | a few GB in total. A CUDA build of torch alone is over 1 GB, so I'll ask before pulling it | Phase 1 image build |
| `Mxode/TinyStories-LLaMA2-25M-256h-4l-GQA` | 103 MB | Phase 1 |
| `google/gemma-3-270m-it` | 536 MB of weights plus 33 MB `tokenizer.json` | Phase 2 |

The official `qwen2_1.5b_instruct.q.har`, which Hailo publishes outside the DFC and
which upstream used as a structural reference, is optional and probably over 1 GB. I'd
ask before fetching it.

### Hugging Face token in a Claude cloud session

If later phases run in a Claude cloud session, add the token in that environment's
settings, not in chat: open the cloud environment menu in the session's title bar,
choose **Edit**, and add it under **Network secrets** (or as an environment variable)
named `HF_TOKEN`. New sessions pick it up. On your own machine, `huggingface-cli
login` or an exported `HF_TOKEN` both work.

## Raspberry Pi 5 + AI HAT+ 2 (Hailo-10H)

| Item | Version | Where from | Notes |
|---|---|---|---|
| HailoRT PCIe driver and firmware | 5.3.0 | Hailo Developer Zone, Hailo-10 suite | Replaces apt's `hailo-h10-all` 5.1.1. Driver, firmware, runtime and server must come from one suite drop |
| HailoRT (libhailort, `hailortcli`) | 5.3.0, arm64 | same | |
| HailoRT Python bindings (`hailo_platform`, including `genai`) | 5.3.0, for the Pi's Python | same | Raspberry Pi OS Trixie ships Python 3.13; check which wheels Hailo offers |
| Hailo GenAI Model Zoo (provides `hailo-ollama`) | 5.3.0, arm64 `.deb` | same | Its 5.3.0 manifests use `hef_h10h`, matching upstream's register script |
| 27 W USB-C supply and an active cooler | – | – | As for CerberOS |

All of that is system-level change on the Pi, so it happens only with your go-ahead.
Phase 1's on-device prefill check is the first point that needs the Pi.

## Check a machine

```bash
scripts/00_fetch_upstream.sh                     # pinned upstream sources into work/upstream/
scripts/00_check_env.sh --wheel-dir work/dfc     # read-only readiness report
```
