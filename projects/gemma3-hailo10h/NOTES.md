# NOTES: Gemma 3 270M on the Hailo-10H

The lab notebook for this project. Every meaningful step gets an entry: what was tried,
what happened (with numbers), and the next hypothesis. It is written so that a new
session with no memory can pick up from here.

## Start here

- **Goal:** `google/gemma-3-270m-it` generating coherent multi-token text on a
  Hailo-10H (Raspberry Pi AI HAT+ 2), served by hailo-ollama.
- **Approach:** the community compiler `l-nmch/hailo-10h-llm-compiler`, pinned at
  `v0.3.0-dfc5.3.0`. `scripts/00_fetch_upstream.sh` fetches it into `work/upstream/`.
- **Read first:** `docs/phase0-community-repo.md` (how upstream works, the runtime
  contract, risks), then `docs/setup-checklist.md`.
- **Current phase:** 0 is done and reported. Phase 1 waits on the user (see "Waiting on
  the user").

## Rules from the user

- Run phases 0–7 in order. Finish and report each one before starting the next.
- Update this file after every meaningful step.
- Reproducible steps go in `scripts/`, findings in `docs/`. Pin every version (DFC,
  HailoRT, torch, transformers) in `versions.env`.
- Compare against the float reference with small test scripts. Never claim success
  without numbers.
- **Ask first** before installing system packages, touching the Pi, downloading more
  than about 1 GB, or abandoning a phase.
- The DFC is not redistributable. The user downloads it. Never fetch, mirror or commit
  it.
- If Gemma 3 hits a true blocker (an unsupported op with no rewrite), record the exact
  op and error, then propose the closest fallback. Don't silently change the goal.
- Be honest about failures. A well-documented dead end is a valid result.

## Phase status

| Phase | Status |
|---|---|
| 0 Setup: read upstream, summarize, list installs | done |
| 1 Reproduce TinyStories, check prefill against float32 | waiting on the user |
| 2 Gemma 3 architecture audit (`docs/gemma3-audit.md`) | not started; needs an HF token |
| 3 ONNX export at a fixed context | not started |
| 4 Embeddings and tokenizer | not started |
| 5 Quantize and compile (INT8, then an INT4 mix) | not started |
| 6 Validate prefill and multi-token generation | not started |
| 7 Deploy and benchmark on the Pi | not started |

## Waiting on the user

1. Where Phases 1–6 run. This cloud container can't run the DFC as it stands: no
   wheel, 15 GB RAM, no GPU, no Docker daemon, no route to the Pi. Recommended: Claude
   Code on the user's own x86 compile machine.
2. Toolchain version: 5.3.0 everywhere (recommended) or 5.4.0.
3. Permission and an SSH route for read-only checks on the Pi.
4. A Hugging Face read token as `HF_TOKEN`, after accepting the Gemma terms.

## Log

### 2026-10-19: Phase 0, reading upstream

**Tried.** Read all of upstream at `c870419f7d63`: README, CLAUDE.md, CHANGELOG,
`docs/` (status, terminology, references, device setup, all 17 findings),
`pipeline/` s1–s6 and config, `runtime/` with diagnostics, `recipes/`, `docker/`, the
notebooks, `.claude/skills/` and CI. Also read all 19 wiki pages at `540aa5da6522`.
Upstream has no open GitHub issues. Then checked the runtime contract against the
public HailoRT source (v5.3.0 and v5.4.0) and the hailo-ollama source (v5.3.0 and
v5.4.0). Also queried the Raspberry Pi apt repo, the Hugging Face API and PyPI.

**Result.**
- The forum post's `__tbt` cache-read issue is fixed upstream (v0.2.0). The cause was
  a KV-cache layout mismatch between `__prefill` and `__tbt`. Step 6 now puts format
  conversions on every cache edge.
- Upstream's open problem is quantization on general-purpose models. Every one tried
  is incoherent on the chip, even with no KV-cache involved. The wiki blames massive
  activations quantized to 8 bits (it first looked like a `hidden ≥ 576` threshold).
  SmolLM2-135M is in that failing set.
- Upstream tried `google/gemma-3-270m` and says it "still fails". It lists the
  alternating local/global attention as unsupported.
- HailoRT source (v5.3.0; `pre_process.cpp` is identical in v5.4.0):
  - The mask is always plain causal, repeated per head. There is no sliding-window
    support.
  - RoPE: cos/sin are computed for every entry of `rope_theta_data.bin`, whatever its
    length (`mrope_section = {theta.size()/2}`), then tiled ×n_heads for Q and
    ×n_kv_heads for K.
  - Embeddings must be uint16 and are looked up from `embeddings.bin` by the server,
    which runs on the Hailo-10H itself.
  - `optimize_memory_on_device` keeps the tokenizer and embedding table on the host,
    but hailo-ollama never sets it.
- hailo-ollama's manifest format changed: 5.3.0 reads `hef_h10h` (sha256), 5.4.0
  reads `hef_filename`. Upstream's register script writes the 5.3.0 form.
- **Server bug with uneven lm_head shards** (`llm_inference_manager.cpp:56-87`,
  same in 5.4.0). The server packs the output shards into one buffer at offset
  `i × size(shard i)`, which is only right when all shards are the same size.
  Upstream's `np.linspace` split is uneven unless the count divides the vocabulary:
  TinyMistral 16002/16003, Qwen 30387/30388. For Gemma, upstream's default of 9
  shards gives 29127/29128 (wrong); 8 shards gives 32768 each (right). Shard output
  order must also follow vocabulary order.
- Versions: Raspberry Pi apt ships `hailo-h10-all` 5.1.1. Upstream is validated on
  DFC and HailoRT 5.3.0. Hailo's current release is 5.4.0.
- `google/gemma-3-270m-it` is gated (HTTP 401 without a token). It is 536 MB of
  weights plus a 33 MB `tokenizer.json`.
- This cloud container has 4 vCPUs, 15 GB RAM, no GPU, about 30 GB free disk, a
  Docker client with no daemon, no HF token, no DFC wheel, and no route to the Pi.

**Next hypotheses, to test in later phases.**
- A KV-cache no larger than the sliding window makes Gemma's local and global
  attention identical, so the runtime's causal-only mask is exact. This depends on
  the window size and Hugging Face's window rule (Phase 2).
- Two RoPE bases can be served by one `rope_theta_data.bin` laid out as
  `[θ_local ‖ θ_global]`, with constant selection matmuls in the graph. The runtime
  side is confirmed. The DFC side is untested (Phase 3, on a tiny Gemma-shaped toy
  first).
- The √hidden embedding scale and the RMSNorm (1 + w) fold into weights at export
  (Phase 3, checked with step 1's cosine gate).
- 8 even lm_head shards of 32768 place on the chip, and hailo-ollama's output matches
  a host-side concatenation (Phases 3 and 6).
- Quantization is the real fight (Phases 5–6). Measure Gemma's activation outliers in
  float before choosing a recipe.

## Upstream quirks noted

Fix or watch for these when we use the code:

- `docker/Dockerfile.nvidia` doesn't install `transformers`, and installs the DFC
  wheel with `--no-deps`.
- The `s1 --seq` help text says the default is 24; it is really 128.
- The diagnostics' default arguments are TinyStories-specific.
- The calibration builder adds a BOS per sentence and pads to SEQ.
- The notebooks are stale.
