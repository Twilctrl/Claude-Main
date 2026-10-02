# hailcore OS

A Raspberry Pi OS–based distribution for one job: running a local LLM on a
**Raspberry Pi 5 with the AI HAT+ 2 (Hailo-10H, 40 TOPS, 8 GB on-module RAM)**.
It boots straight into a neon cyberpunk console with the LLM server already
running.

```
██╗  ██╗ █████╗ ██╗██╗      ██████╗ ██████╗ ██████╗ ███████╗
██║  ██║██╔══██╗██║██║     ██╔════╝██╔═══██╗██╔══██╗██╔════╝
███████║███████║██║██║     ██║     ██║   ██║██████╔╝█████╗
██╔══██║██╔══██║██║██║     ██║     ██║   ██║██╔══██╗██╔══╝
██║  ██║██║  ██║██║███████╗╚██████╗╚██████╔╝██║  ██║███████╗
╚═╝  ╚═╝╚═╝  ╚═╝╚═╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝
```

> **Status: early.** The build and every script have been linted and the `hc`
> tools tested against a mock server, but the image hasn't been booted on real
> hardware yet. Expect rough edges, and please report them.

## What's in the image

It's built with [pi-gen](https://github.com/RPi-Distro/pi-gen), the tool that
builds Raspberry Pi OS: Raspberry Pi OS Lite (64-bit, Debian trixie) plus one
extra stage, [`stage-hailcore`](stage-hailcore).

**LLM stack**
- `hailo-h10-all` from the Raspberry Pi apt repo: Hailo-10H driver (DKMS),
  firmware and HailoRT. The driver is built for the Pi 5 kernel at image build
  time, and rebuilt at first boot if that didn't take.
- `hailo-ollama` from the Hailo GenAI Model Zoo, running as the
  `hailo-ollama` systemd service under its own unprivileged `hailo` user, with
  an Ollama-compatible API on `127.0.0.1:8000`.
- A first-boot service that checks the driver and pulls the default model
  (`qwen2.5-instruct:1.5b`).

**Tuning**
- PCIe Gen 3 for the HAT, PCIe link power management off, CPU governor
  `performance`, Wi-Fi power save off.
- zram swap instead of a swap file, logs kept in RAM, and background apt,
  man-db and other unneeded services disabled, so the SD card isn't hammered.
- `hailo-ollama` runs at a higher CPU and I/O priority than everything else.
- Root account locked (pi-gen leaves it as `root`/`root`).

**Cyber theme**
- Neon palette (cyan, magenta, acid green, Cyberpunk yellow) loaded into the
  kernel console from the first boot message, via the kernel command line.
- Quiet boot without the rainbow splash or kernel logos, a big Terminus
  console font, and a banner on the login screen showing the host and IP.
- A login readout with NPU state, model, temperature and IP.
- A two-line neon bash prompt that shows failed exit codes, plus matching
  `ls` colours, a tmux theme and a fastfetch config.
- `hc chat`, a streaming chat client with a "decrypting" intro and
  tokens-per-second readout after each reply.

## The `hc` command

| Command | What it does |
| --- | --- |
| `hc` / `hc status` | NPU, server, model, temperature, throttling, memory, firmware |
| `hc chat [model]` | Interactive chat. `/model`, `/system`, `/clear`, `/save`, `/exit` |
| `hc ask "prompt"` | One-shot answer. Piped input is appended: `dmesg \| hc ask "anything wrong?"` |
| `hc bench [model]` | Three runs, average tokens per second |
| `hc models [--available]` | Installed models, or ones the server can pull |
| `hc pull [model]` | Download a model, with progress |
| `hc use <model>` | Make a model the default; pulls it if needed |
| `hc logs` / `hc restart` | Follow or restart the LLM server |
| `hc doctor` | Checks the board, PCIe, driver, server, model, power and first boot, and tells you how to fix what's wrong |

Settings live in `/etc/hailcore/hailcore.conf`: default model, API URL, CPU
governor, and `HAILCORE_LAN_API=1` to relay the API to your LAN on port 8001.
The relay is off by default because the API has no authentication.

## Build it

You need Docker (any x86_64 or arm64 Linux host works) and about 20 GB of disk.

```sh
cd projects/hailcore-os
./build.sh
```

The image lands in `deploy/`. Flash it with Raspberry Pi Imager ("Use custom")
or `xz -dc deploy/*.img.xz | sudo dd of=/dev/sdX bs=4M status=progress`.

Options are environment variables:

| Variable | Default | |
| --- | --- | --- |
| `HAILCORE_USER` | `netrunner` | Login user |
| `HAILCORE_PASS` | random | Printed at the end and saved to `deploy/password.txt` |
| `HAILCORE_SSH_PUBKEY` | — | Path to a `.pub` key to authorise for SSH |
| `HAILCORE_HOSTNAME` | `hailcore` | Reach it at `hailcore.local` |
| `HAILCORE_TIMEZONE` | `Etc/UTC` | |
| `HAILCORE_KEYMAP` | `us` | Console keyboard layout |
| `HAILCORE_WIFI_COUNTRY` | — | e.g. `GB`. Needed before Wi-Fi works |
| `HAILO_GENAI_DEB` | Hailo's public 5.1.1 URL | Path or URL to the GenAI Model Zoo `.deb`, or `none` |
| `PI_GEN_REF` | `arm64` | pi-gen branch or tag to build from |
| `NO_DOCKER` | `0` | `1` builds natively (run with `sudo` on Debian/Pi OS) |

Example:

```sh
HAILCORE_SSH_PUBKEY=~/.ssh/id_ed25519.pub HAILCORE_WIFI_COUNTRY=US ./build.sh
```

If the Hailo download fails, the build carries on without `hailo-ollama`.
Download the `.deb` from the
[Hailo Developer Zone](https://hailo.ai/developer-zone/), copy it to the Pi,
install it with `sudo apt install ./hailo_gen_ai_model_zoo_*.deb`, then run
`sudo systemctl restart hailo-ollama hailcore-firstboot`.

## First boot

1. Use the official 27 W USB-C supply and an active cooler. The HAT and the
   CPU both get warm under sustained inference.
2. Boot, then wait a few minutes. The first boot downloads the default model.
   `hc logs` shows progress.
3. Log in on the console or with `ssh netrunner@hailcore.local`, then run
   `hc doctor`, then `hc chat`.

## Layout

```
build.sh                    clones pi-gen, adds the stage, writes config, builds
stage-hailcore/
  00-hailo/                 Hailo-10H driver + HailoRT + hailo-ollama, DKMS build
  01-overlay/files/         everything copied verbatim into the image
    etc/hailcore/           hailcore.conf, console palette, shell theme
    etc/systemd/system/     hailo-ollama, firstboot, perf, palette units
    usr/local/bin/hc        the control CLI
    usr/local/lib/hailcore/ chat client, first-boot and tuning scripts, banner
  02-system/                config.txt / cmdline.txt, services, zram, lockdown
  03-cyber/                 console font, login screen, user dotfiles
```

## Works with

[feather-llm-keyboard](../feather-llm-keyboard) runs on top of this image
unchanged: it expects hailo-ollama on port 8000, and that's what hailcore
provides. Run its `install.sh` and it'll find the existing `hailo-ollama`
service.

## Roadmap

- Boot-tested release images published from CI.
- An optional read-only root filesystem (overlayfs) for appliance use.
- A Plymouth boot animation and a matching theme for a minimal desktop build.
- A web chat UI on the LAN, behind authentication.
- Voice: whisper on the NPU in, Piper TTS out.
