# CerberOS

**Three heads. One gate. No cloud.**

CerberOS is a Raspberry Pi OS–based distribution for running local AI on a
**Raspberry Pi 5 with the AI HAT+ 2 (Hailo-10H, 40 TOPS, 8 GB on-module RAM)**.
It takes its ease of use from Kali and Parrot: every model and tool sits in a
numbered category in a start menu, and everything installs itself the first
time you pick it. It looks like cyber-hell: blood red, ember and hellfire, from
the first boot message to the desktop.

![The CerberOS start menu](docs/menu.png)

> **Status: early.** Everything here is linted. The gate, CLI, menu and
> generators have been tested against mock NPU, CPU and remote servers. The
> image hasn't been built or booted on real hardware yet. Expect rough edges,
> and please report them.

## The three heads and the gate

```
                 ┌───────────────────── the gate :11434 ─────────────────────┐
  Open WebUI ───▶│  Ollama API (/api/*)          OpenAI API (/v1/*)          │
  AnythingLLM ──▶│  routes by model name, translates between dialects        │
  aider, llm ───▶│  chatbots/gemma · coding/poolside · gemma3:1b · x@npu     │
  your laptop ──▶└──────┬──────────────────────┬──────────────────────┬───────┘
                        ▼                      ▼                      ▼
              NPU head :8000           CPU head :11436         REMOTE head (optional)
              hailo-ollama on          Ollama on the Pi's      any Ollama / OpenAI-
              the Hailo-10H            CPU, any GGUF model     compatible box on your LAN
```

- **NPU head.** Hailo's `hailo-ollama` runs Hailo-compiled models (Qwen 2.5,
  Qwen Coder, DeepSeek R1 distill, Llama 3.2) on the accelerator, which leaves
  the CPU free.
- **CPU head.** Stock Ollama runs anything from the Ollama library that fits in
  the Pi's RAM: Gemma 3, Qwen 3, SmolLM2, Moondream, embedding models, and so on.
- **Remote head.** Point it at a bigger machine to use models a Pi can't hold,
  such as Poolside's 33B Laguna XS.2.
- **The gate** sits on Ollama's standard port, 11434, so **any app that
  supports Ollama finds every head with no setup**. It also serves the
  OpenAI API at `/v1`, for tools that only speak OpenAI. It merges the model
  lists from all three heads and routes each request by model name. It
  translates where a head doesn't speak the client's dialect: OpenAI requests
  to the NPU become Ollama requests, and Ollama requests to an OpenAI-only
  remote become OpenAI requests.

Model names the gate accepts:

| Name | Goes to |
| --- | --- |
| `chatbots/gemma` | Catalog alias: the head and model listed in the catalog |
| `gemma3:1b` | Raw name: whichever head has it, checking NPU, then CPU, then remote |
| `qwen2:1.5b@cpu` | Raw name on a head you choose: `@npu`, `@cpu` or `@remote` |

## Models, sorted by strength

Models live in `/srv/local-models/<strength>/<model>/` (also `~/local-models`).
Each folder holds a model card and a `./chat` launcher, and every tool can call
the model by its path:

```
local-models/
├── chatbots/   qwen-npu ★  gemma ◆  llama  smollm ◆
├── coding/     qwen-coder-npu ★  qwen-coder  poolside (remote)
├── reasoning/  deepseek-r1-npu  qwen3
├── vision/     gemma-vision  moondream
└── agents/     nomic-embed ◆
                ◆ baked into the image   ★ downloaded at first boot   others: on first use
```

| Model | Head | Good at |
| --- | --- | --- |
| `chatbots/qwen-npu` | NPU | Default. Fast general chat with the CPU left free |
| `chatbots/gemma` | CPU | Gemma 3 1B, the quickest CPU chat model (~20 tok/s) |
| `chatbots/llama` | NPU | Llama 3.2 3B, better prose |
| `chatbots/smollm` | CPU | SmolLM2 360M, for shell pipelines and classification |
| `coding/qwen-coder-npu` | NPU | Code completion and shell one-liners |
| `coding/qwen-coder` | CPU | Qwen 2.5 Coder 3B, better code but slower |
| `coding/poolside` | Remote | Poolside Laguna XS.2: 33B MoE agentic coder, needs about 24 GB RAM |
| `reasoning/deepseek-r1-npu` | NPU | Step-by-step reasoning and maths |
| `reasoning/qwen3` | CPU | Qwen 3 1.7B with switchable thinking mode |
| `vision/gemma-vision` | CPU | Gemma 3 4B: reads images (8 GB Pi) |
| `vision/moondream` | CPU | Small image captioner |
| `agents/nomic-embed` | CPU | Embeddings for RAG in Open WebUI and AnythingLLM |

`cerb pull` checks the Pi's RAM before downloading a CPU model, and won't pull a
remote-head model until a remote head is configured. Any other Ollama model
works by its raw name (`cerb pull phi4-mini`). To add your own entry to the
catalog, put it in `/etc/cerberos/catalog.d/*.toml` and run `sudo cerb sync`.

## Apps, by category

| # | Category | Apps |
| --- | --- | --- |
| 01 | Chatbots | **Cerberus Chat** (terminal) ◆, **Open WebUI** ◆★, **llm** CLI ◆ |
| 02 | Coding | **aider**, IDE bridge (Continue, Cline and VS Code settings) ◆ |
| 03 | Reasoning | (models only) |
| 04 | Vision | Hailo vision demos (detection, pose, segmentation, depth) |
| 05 | Voice | whisper.cpp (speech to text), Piper (text to speech) |
| 06 | Agents & RAG | AnythingLLM, n8n |
| 07 | Model Arsenal | Model manager, "Devour any model" |
| 08 | Underworld | Status, Doctor, Benchmark, Gate, Logs, Settings, btop |

◆ installed in the image. ★ its container is downloaded at first boot. Every
other app asks once and installs itself when you first launch it. Web apps run
in Docker on the host network, already connected to the gate:
Open WebUI on :3000, AnythingLLM on :3001, n8n on :5678.

## The `cerb` command

Type `cerb` to open the start menu: ←→ switch panes, 1–8 jump to a category,
Enter launches, `i` installs, `x` removes. Or use it directly:

| Command | Alias | What it does |
| --- | --- | --- |
| `cerb chat [model]` | `summon` | Streaming chat. `/model`, `/system`, `/clear`, `/save` |
| `cerb ask [-m model] "…"` | | One-shot answer. Piped input is appended: `dmesg \| cerb ask "anything wrong?"` |
| `cerb models` | `ls` | Every model by category, installed or not |
| `cerb pull <model>` | `devour` | Download, with a progress bar |
| `cerb rm <model>` | `banish` | Delete |
| `cerb use <model>` | | Set the default model |
| `cerb apps [category]` | | Every app by category |
| `cerb launch <app\|model>` | `unleash` | Start anything; installs it first if needed |
| `cerb status` | `heads` | Heads, gate, temperature, throttling, memory |
| `cerb gate` | | Endpoints, plus copy-paste settings for Open WebUI, Continue, Cline, aider and the OpenAI SDK |
| `cerb doctor` | `rite` | Checks the board, PCIe, driver, every head, the gate and power |
| `cerb bench`, `logs`, `restart`, `config`, `sync` | | Benchmark, logs, restart, edit settings, rebuild the menu |

## Two editions

- **lite** (default): console only. Boots to a hellfire login screen, and
  `cerb` is the start menu. Leaves the most RAM for models.
- **desktop**: XFCE with the cyber-hell theme. The Whisker start menu gets
  Kali-style numbered categories that hold every app and model. There's a
  three-hound wallpaper and login screen, a hellfire terminal, and the
  workspaces are named NPU, CPU and REMOTE. On first login, the menu opens in a
  terminal.

![Desktop wallpaper](docs/wallpaper.jpg)

## Build it

You need Docker (any x86_64 or arm64 Linux host works) and about 25 GB of disk.

```sh
cd projects/cerberos
./build.sh                          # lite edition
CERBEROS_FLAVOR=desktop ./build.sh  # desktop edition
```

The image lands in `deploy/`. Flash it with Raspberry Pi Imager ("Use custom")
or with `xz -dc deploy/*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress`.

| Variable | Default | |
| --- | --- | --- |
| `CERBEROS_FLAVOR` | `lite` | `lite` or `desktop` |
| `CERBEROS_USER` | `hellhound` | Login user |
| `CERBEROS_PASS` | random | Printed at the end and saved to `deploy/password.txt` |
| `CERBEROS_SSH_PUBKEY` | — | Path to a `.pub` key to authorise for SSH |
| `CERBEROS_HOSTNAME` | `cerberos` | Reach it at `cerberos.local` |
| `CERBEROS_WIFI_COUNTRY` | — | e.g. `GB`. Needed before Wi-Fi works |
| `CERBEROS_TIMEZONE`, `CERBEROS_KEYMAP` | `Etc/UTC`, `us` | |
| `CERBEROS_BAKE_MODELS` | `1` | Bake the catalog's `preload = "build"` models into the image |
| `HAILO_GENAI_DEB` | Hailo's public 5.1.1 URL | hailo-ollama `.deb`: a path, a URL, or `none` |
| `OLLAMA_TARBALL` | ollama.com arm64 build | A path, a URL, or `none` |
| `PI_GEN_REF` | `arm64` | pi-gen branch or tag |
| `NO_DOCKER` | `0` | `1` builds natively (with `sudo`, on Debian or Pi OS) |

Baking works because Ollama's model files don't depend on the CPU
architecture: an x86 Ollama in Docker downloads them on the build host, and
they run on the Pi unchanged. Downloads are cached in `work/`.

## First boot

1. Use the official 27 W USB-C supply and an active cooler.
2. Boot, then log in at the console or with `ssh hellhound@cerberos.local`.
   The first boot downloads the NPU models and the Open WebUI container in the
   background. Follow it with `cerb logs`.
3. Run `cerb doctor`, then `cerb`.

To use a bigger machine as the remote head, run Ollama on it and make it
listen on the LAN (`OLLAMA_HOST=0.0.0.0`). Then on the Pi, run `cerb config` and
set `CERBEROS_REMOTE_URL=http://<pc>:11434`, then `cerb pull coding/poolside`.
To share your models with the rest of the LAN, set `CERBEROS_GATE_LAN=1`. Also
set `CERBEROS_GATE_KEY` to require a bearer token from other machines.

## What else is tuned

- PCIe Gen 3 for the HAT, with PCIe link power management off, the CPU
  governor on `performance`, and Wi-Fi power save off.
- Ollama keeps one model loaded at a time, because RAM is the Pi's limit. Both
  model servers run at a higher priority than everything else.
- To spare the SD card, swap is compressed in RAM (zram) and logs stay in RAM.
  Background apt and man-db jobs are off.
- Root is locked; pi-gen would otherwise leave it as `root`/`root`. The gate
  runs as its own unprivileged, sandboxed user.
- The hellfire palette loads from the kernel command line, so it's there from
  the first boot message. The console uses a Terminus font, with banners on the
  login screen and at login, a neon prompt that shows failed exit codes, and
  matching tmux and fastfetch themes.

Security trade-offs to know about: the first user is in the `docker` group,
which is equivalent to root, so web apps can start without sudo. The web apps
listen on the LAN.

## Layout

```
build.sh                    clones pi-gen, fetches heads, bakes models, builds
stage-cerberos/
  00-hailo/                 NPU head: Hailo-10H driver (DKMS), HailoRT, hailo-ollama
  01-ollama/                CPU head: Ollama + baked models
  02-overlay/files/         copied verbatim into the image:
    etc/cerberos/             cerberos.conf, catalog.toml, catalog.d/, palette, shell theme
    usr/local/bin/cerb        the command
    usr/local/lib/cerberos/   the cerberos Python package (gate, CLI, menu, sync), app recipes
  03-apps/                  Docker, pipx apps, gate user, /srv/local-models + menu generation
  04-system/                config.txt / cmdline.txt, services, zram, lockdown
  05-theme/                 console font, login screen, dotfiles, os-release
  06-desktop/               desktop edition only: XFCE, LightDM, wallpaper, Whisker menu
```

The `cerberos` package uses only the Python standard library.

## Works with

[feather-llm-keyboard](../feather-llm-keyboard) runs on CerberOS unchanged. To
let it use any head, change its URL to the gate:
`--llm-url http://localhost:11434 --model chatbots/gemma`.
