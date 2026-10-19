# Phase 0: the community compiler, read end to end

This page covers everything in `l-nmch/hailo-10h-llm-compiler`: code, docs, all 17
findings, recipes, notebooks, Claude skills and CI. It also covers the project's
GitHub wiki and the parts of Hailo's public HailoRT source that define the runtime
contract. Nothing has been compiled or run yet.

| Source | Pin | Fetch |
|---|---|---|
| `l-nmch/hailo-10h-llm-compiler` | tag `v0.3.0-dfc5.3.0`, commit `c870419f7d63` | `scripts/00_fetch_upstream.sh` |
| its wiki | commit `540aa5da6522` | same |
| `hailo-ai/hailort` (MIT) | tag `v5.3.0` (`d503417f2a0d`); `v5.4.0` diffed against it | `--with-hailo-src` |
| `hailo-ai/hailo_model_zoo_genai` (hailo-ollama, MIT) | tags `v5.3.0` and `v5.4.0` | `--with-hailo-src` |

Paths below like `pipeline/s1_export_onnx.py` are in the compiler repo. HailoRT paths
are relative to the `hailort/` directory of that repo at v5.3.0. Bare file names like
`llm_server.cpp:307` are in `hailort_server/genai/llm/`.

## 1. Headlines

1. **The `__tbt` cache-read problem from the forum post is fixed** (upstream v0.2.0).
   The `__prefill` and `__tbt` network groups disagreed on the KV-cache memory
   layout. Step 6 now adds a `tf_rgb_to_hailo_rgb` / `hailo_rgb_to_tf_rgb` format
   conversion on every cache edge of both groups. On TinyStories-25M, greedy
   generation through the KV-cache matches float32 Hugging Face, and hailo-ollama
   serves coherent text.
2. **The open problem is now quantization on general-purpose models.** Every one
   tried is incoherent on the chip, even with no KV-cache involved: NanoLM-25M-Instruct
   (hidden 312), SmolLM2-135M (576), Llama-160M (768), TinyMistral-248M (1024),
   Qwen2.5-0.5B (896). Only TinyStories-style models (25M, 15M) work. The repo's
   `docs/status.md` still calls this a `hidden ≥ 576` threshold. The newer wiki
   retracts that, because NanoLM at hidden 312 also fails. It blames massive
   activations quantized to 8 bits: the SwiGLU product, the softmax `reduce_max`
   and the RMSNorm square. Unsolved upstream.
3. **Gemma 3 270M was tried upstream and "still fails"** (wiki,
   Porting-Another-Model). Upstream lists Gemma 2/3's alternating local/global
   attention as unsupported. It never got as far as Gemma's other features.
4. **SmolLM2-135M, the fallback you named, is not a safe fallback.** It compiles,
   but its output on the chip is incoherent for the same reason as item 2.

## 2. The pipeline

Everything runs in a Docker image (DFC wheel plus PyTorch). Each step reads and writes
only `$DFC_WORKDIR` and can be re-run alone. Step 1 takes `--model <hf-id>` and writes
every architecture constant to `run_config.json`, which steps 2–6 read.

| Step | Script | In → out | What it does | Check |
|---|---|---|---|---|
| 1 | `pipeline/s1_export_onnx.py` | HF checkpoint → `model.onnx`, `hf_reference.npz`, `wte.npy`, `tokenizer/` | Re-implements the model with "matmul tricks": RoPE rotate-half, GQA `repeat_kv`, head tiling and QK-norm become constant-matrix multiplies, because the parser rejects shuffle ops. Appends `lm_head` on the last position only. Exports ONNX opset 17 with the legacy TorchScript exporter | cosine > 0.999 vs float32 HF, for the PyTorch re-implementation and again through onnxruntime |
| 2 | `pipeline/s2_parse_har.py` | ONNX → `parsed.har` | DFC parser, `hw_arch="hailo10h"`. Declares six inputs: embeddings `[1,SEQ,HIDDEN]`, mask `[1,1,SEQ,NHEAD·SEQ]`, four RoPE inputs `[1,SEQ,HD]` | cosine in `SDK_NATIVE` emulation (unreliable for attention, Finding 11) |
| 3 | `pipeline/s3_surgery_and_resources.py` | → `resources.har`, `hailo-config.json` | Edits the `.hn` JSON: RoPE surgery (Finding 3), mask surgery (Finding 4), and an lm_head split into ⌈vocab/32000⌉ shards, each with its own copy of the final slice/norm chain (Finding 12). Attaches the four external resources and a server-adapted tokenizer | `SDK_NATIVE` cosine after surgery; removed layers checked absent |
| 4 | `pipeline/s4_optimize_kvcache.py` | → `quantized.har` | `set_kv_cache_global_params(PREFILL, CACHE)` duplicates the graph into `__prefill` and `__tbt` inside `optimize()`. Default recipe: `ew_add_fusing` off, `optimization_level=0`, embeddings `a16_w16`, convs `a8_w4`, `bias_correction` on. `--recipe` loads an `.alls` file instead; `recipes/hailo-llm.alls` is Hailo's own Qwen2 recipe with role placeholders | none: the quantized emulator is broken on KV-cache graphs, so hardware is the only judge |
| 5 | `pipeline/s5_fix_convolutions.py` | → `convfixed.har` | Reorders misaligned multi-input conv inputs; finds none with the current recipe | structural |
| 6 | `pipeline/s6_compile_hef.py` | → `model.hef`, `compiled.har` | Declares exactly two groups, `<scope>__prefill` and `<scope>__tbt`. Adds the cache-edge format conversions (Finding 9), skips the spatial-reshape search (Finding 17), uses `compiler_optimization_level=0`. `--include-base-scope` adds a third group for diagnostics only | HEF produced |
| – | `runtime/register_hailo_ollama.py` | HEF → hailo-ollama store | Copies the HEF to `~/.local/share/hailo-ollama/models/blob/sha256_<hash>` and writes `/usr/share/hailo-ollama/models/manifests/<family>/<tag>/manifest.json` with key `hef_h10h`. hailo-ollama only scans manifests at startup | – |

Device-side tools (`runtime/`):

- `genai_generate.py` runs each attempt in its own subprocess. That works around an
  EINTR bug in HailoRT's PCIe accept path.
- `diagnostics/hef_audit.py` decodes a HEF's structure and embedded resources
  offline.
- `diagnostics/hotpatch_hailo_config.py` rewrites the embedded config without
  recompiling.
- `diagnostics/kv_greedy.py` drives `__prefill` and `__tbt` exactly like the server.
  `--mask-mode self` hides the cache, so it separates "the tbt graph is wrong" from
  "what tbt reads from the cache is wrong".
- `diagnostics/manual_prefill_tbt_test.py` runs one prefill and one tbt step against
  a reference NPZ.
- `diagnostics/generate_base_scope.py` is the cache-free control. It needs a HEF
  built with `--include-base-scope`.
- `diagnostics/runtime_inputs.py` rebuilds the mask, RoPE and uint16 embeddings by
  hand.

Scale data from upstream:

- TinyStories-25M compiles in about 9 minutes.
- SmolLM2-135M (30 layers) took about 3.5 hours to compile before Finding 17.
- Qwen2.5-0.5B spent about 5.5 hours in placement. The lm_head alone (151936 × 896)
  took about 3 hours. Its quantized HAR is about 13.7 GB.
- Quantizing Qwen3-0.6B (28 layers) needed more than 56 GB of RAM, because every
  layer exists three times during `optimize()`.

## 3. The five export incompatibilities (Findings 1–5)

Each one silently ruins the output on its own. Upstream fixed all five before it got
its first real English words on hardware.

| # | Incompatibility | What goes wrong | Fix (step) |
|---|---|---|---|
| 1 | No `lm_head` in the graph | The runtime has no vocabulary concept: it argmaxes the HEF's raw output. A graph that ends at the hidden state "samples" over hidden dimensions, which gives byte salad | Append an explicit `lm_head` matmul built from the checkpoint's own `lm_head.weight`. With untied embeddings it must not reuse the embedding table (s1) |
| 2 | The output must be `[1, 1, vocab]` | The runtime predicts one token, from the last position. Full-sequence logits use the wrong format, and multiplying lm_head by SEQ makes it unplaceable ("Agent infeasible") | Slice `x[:, -1:, :]` before lm_head (s1) |
| 3 | RoPE inputs are tiled per head, and K and Q widths differ | The runtime writes cos/sin already tiled: ×`num_key_value_heads` for K, ×`num_attention_heads` for Q. The parser declared `HD`-wide inputs and inserted duplication convs, so the tiling happened twice | Delete the four duplication convs and re-declare `input_layer3-6` at `HD·n_kv` and `HD·n_heads` (s3) |
| 4 | Mask broadcast semantics | DFC's `input_repeats` repeats elements (AABBCC), but broadcasting over heads needs tiling (ABCABC). The optimizer rejects `input_tiles` | The runtime already writes the mask tiled over heads, so wire every mask `ew_add` straight to `input_layer2` and neutralize the repeats (s3). SDK cosine went from 0.951 to 1.000 |
| 5 | A config key name | The server reads `prefill_input_tokens_count`. A `_size` key is ignored and it silently falls back to 96 | Write the right key, plus `kv_cache_size` and both head counts (s3) |

## 4. The runtime contract

This is the repo's description, checked against the HailoRT v5.3.0 source.

**Where things run.** The LLM server (`hailort_server`) runs on the Hailo-10H's own
embedded Linux, as a PCIe endpoint. It is not a process on the Pi. It holds the
embedding table, builds the mask and RoPE inputs, runs both network groups and
samples. The client on the Pi (`hailo_platform.genai`, hailo-ollama) streams the HEF
over in chunks and sends prompts.

**HEF structure**

- One network group name must end in `__prefill`. A `__tbt` group is optional in the
  code but present in every official HEF.
- Both groups always run with the KV-cache enabled (`llm_inference_manager.cpp:34`),
  and only one LLM can hold the cache at a time
  (`libhailort/src/genai/llm/llm.cpp:197`).
- There are six inputs, matched by name suffix. The server's default suffixes
  (`pre_process.hpp:27-32`) differ from upstream's layout, which is why upstream
  always writes `input_layers_names_suffixes` explicitly.

| Role | Format | Width | What the server writes |
|---|---|---|---|
| embeddings | `UINT16`, enforced (`pre_process.cpp:87`) | hidden | rows copied from `embeddings.bin` |
| attention_mask | `UINT8`, exactly one quant scale (`llm_server.cpp:324`) | `num_attention_heads × kv_cache_size` | ring-buffer causal mask, repeated per head (`pre_process.cpp:133-174`) |
| pe_q_cos, pe_q_sin | float32, quantized by HailoRT to the HEF's input type (`llm_server.cpp:204-208`) | `len(theta) × num_attention_heads` | `cos`/`sin(theta[i] · position)`, tiled (`pre_process.cpp:189-242`) |
| pe_k_cos, pe_k_sin | same | `len(theta) × num_key_value_heads` | same |

- The output is the last-position logits, `[1, 1, vocab]`. The server samples from it
  with top-k, top-p, temperature and repetition penalty.
- **Split lm_heads need equal-sized shards.** When the lm_head is split into several
  outputs, the server packs them into one buffer. Output `i` goes at offset
  `i × (size of output i)` (`llm_inference_manager.cpp:56-87`; only a debug `assert`
  checks that the sizes match). That is only correct when every shard has the same
  size. With uneven shards, the later shards land shifted by a few positions: logits
  end up under the wrong token ids, and the last shard writes past the end of the
  buffer. Upstream's split (`np.linspace`) is uneven whenever the shard count doesn't
  divide the vocabulary: TinyMistral's 32005 became 16002 + 16003, and Qwen's 151936
  in 5 shards ends in a 30388. By this reading, hailo-ollama output from those HEFs
  was shifted regardless of model quality; not yet confirmed on hardware. Upstream's
  own probes concatenate outputs correctly, so their numbers are unaffected.
  HailoRT 5.4.0 has the same code. The packing
  follows the model's output order, so shard names must also sort in vocabulary
  order.
- Four external resources are all mandatory (`common/genai/constants.hpp`):
  - `embeddings.bin`: uint16 codes, vocab × hidden × 2 bytes. The server infers the
    vocabulary size by division (`llm_server.cpp:346-348`).
  - `rope_theta_data.bin`: float32, any length.
  - `tokenizer.json`: a Hugging Face fast tokenizer.
  - `hailo-config.json`: only `eos_token_id` is required (`llm_server.cpp:104`).
    Missing values for `kv_cache_size`, `num_attention_heads`, `num_key_value_heads`
    and `prefill_input_tokens_count` silently default to 2048, 12, 2 and 96
    (`pre_process.hpp:34-37`). The server also reads `stop_token_id` (a list),
    `chat_template`, `default_generation_params` and reasoning token ids.

**Server behaviour that shapes the export**

- The mask's "allowed" value is written as `quantize(1.0)` under the mask's own
  quantization parameters, and "blocked" as raw 0 (`llm_server.cpp:320-327`).
  Upstream's additive 0/−100 mask works because `quantize(1.0)` saturates at the top
  code, 255, which dequantizes to 0.0.
- The tokenizer's post-processor is never applied, so no BOS is added automatically.
  Detokenization is one token at a time, so a decoder `Strip` step eats every space.
  Step 3 moves BOS into `chat_template` and removes `Strip`.
- Positions are absolute token indices since the context started. Prompts run in
  chunks of `prefill_input_tokens_count`, then one `__tbt` run per generated token.
- **Tokenizer and embedding table on the host.** HailoRT has an
  `optimize_memory_on_device` flag (`libhailort/include/hailo/genai/llm/llm.hpp`).
  With it, `tokenizer.json` and `embeddings.bin` stay in the client process on the
  Pi, which tokenizes and looks up embedding rows itself (`llm.cpp:216-275`).
  hailo-ollama never sets it, in 5.3.0 or 5.4.0, so through hailo-ollama the table
  lives in the Hailo-10H's memory.

**Version differences that matter**

- HailoRT 5.4.0 vs 5.3.0: `pre_process.cpp` and `llm_inference_manager.cpp` are
  identical. In `post_process.cpp`, 5.3.0 applies the temperature before the
  repetition penalty, even when decoding greedily; 5.4.0 applies it only when
  sampling. Model loading also became more asynchronous. Neither version supports
  sliding-window masks or per-layer RoPE.
- hailo-ollama changed its manifest format. 5.3.0 reads
  `{"hef_h10h": "<sha256>"}` and loads `blob/sha256_<hash>`. 5.4.0 reads
  `{"hef_filename": "<name>.hef"}` and loads `blob/<name>.hef`. Upstream's register
  script only writes the 5.3.0 form.

## 5. The other findings, by relevance to Gemma

| Finding | Status | Why it matters here |
|---|---|---|
| 6 Tokenizer and BOS | fixed in s3 | Check Gemma's own `tokenizer.json` (BOS, decoder) in Phase 4 |
| 7 Default quantization recipe | validated on TinyStories only | 8-bit everywhere except the embeddings: the likely failure on general-purpose models |
| 8 SDK behaviours | reference | No post-quantization emulation on KV-cache graphs. Set `NPY_PROMOTION_STATE=legacy` and `CUDA_VISIBLE_DEVICES`. `optimization_level > 0` silently turns adaround and finetune back on |
| 9 KV-cache layout | **fixed** | Applies to us unchanged |
| 11 SDK softmax emulation | SDK-only bug | `SDK_NATIVE` cosine is not a trustworthy attention signal on multi-head models. Prefer layer taps and hardware |
| 12 Large-vocabulary lm_head | **fixed**, up to 5 shards tested | Upstream's default for Gemma would be 9 uneven shards (262144 / 32000, rounded up), which the server packs wrongly (section 4). Use 8 even shards of 32768 instead, a width close to the official Qwen HEF's 37984 |
| 13 Multi-context topology error | superseded | Watch for it at 18 layers |
| 14 and 16, plus the wiki's quantization deep dive | **open** | The main risk. The lead is Hailo's own recipe: `a16_w16` residual adds and SwiGLU products, `a16_w4_a16` down projection, `quarot`, `layer_norm_decomposition`, `smart_softmax_stats`. Its fused softmax mask hurt accuracy on TinyStories |
| 15 Sliding window (Mistral) | compiles; correctness never exercised | The graph doesn't care what the mask contains, but the runtime only builds causal masks (section 6) |
| 17 Spatial-reshape search | fixed | Saves about an hour per network group on deep models |
| 10 Encoder models | out of scope | – |

## 6. First look at Gemma 3 against this contract

This is preliminary. The architecture values are your recollection, not yet verified.
Phase 2 reads `config.json` and the Hugging Face modeling code and writes the real
audit to `docs/gemma3-audit.md`.

| Feature | Upstream support | First read |
|---|---|---|
| Sliding-window local layers mixed with global layers | Wiki: unsupported, because different layers need different masks | The server only ever builds a plain causal mask. If the KV-cache holds no more tokens than the window (512, if your recollection is right), the local mask equals the global one and the alternating pattern disappears. Phase 2 must confirm the window size and Hugging Face's exact window rule |
| Different RoPE bases for local and global layers | Not mentioned | The server computes cos/sin for every entry of `rope_theta_data.bin`, whatever its length (`llm_server.cpp:307`, `pre_process.cpp:207-242`). A table `[θ_local ‖ θ_global]` would give every head slot both rotations, and the graph could pick one per layer with constant matmuls. The runtime side looks fine. Unknown whether the DFC accepts it, since calibration derives cos/sin from the same table |
| Embedding scale √hidden | – | The embedding lookup reads `embeddings.bin`, so fold the scale into the table before it is attached |
| RMSNorm with (1 + w) | – | Fold `1 + w` into the exported weights |
| Norms before and after both attention and MLP | No: the exporter is LLaMA-shaped | Exporter change. Trap: in Gemma, `post_attention_layernorm` is the norm after attention, not the pre-MLP norm the LLaMA exporter takes it for |
| GeGLU with tanh GELU | No: SiLU only | Exporter change. Whether the parser accepts the tanh-GELU pattern is unknown |
| QK-norm | Yes (Qwen3 path) | Plus the (1 + w) fold |
| `head_dim` ≠ hidden / heads | Yes (explicit `head_dim`) | – |
| One KV head (4 query heads per KV head) | Yes | – |
| Tied embeddings | Yes | – |
| A ~262k vocabulary | lm_head sharding yes, up to 5 shards | Split into 8 × 32768: the shards must be equal-sized (section 4). `embeddings.bin` would be about 320 MiB and `tokenizer.json` is 33 MB. Expect lm_head placement to take hours |

**Embedding table, Phase 4 preview.** Through hailo-ollama the table must be embedded
in the HEF, and it lives in the Hailo-10H's 8 GB. HailoRT can keep it on the Pi
instead (`optimize_memory_on_device`), but only through the Python/C++ genai API or a
patched hailo-ollama.

**Throughput, Phase 7 preview.** If your numbers are right, the lm_head (about 168M
weights) is bigger than the whole transformer body (about 100M), so it will dominate
per-token decode cost. Trimming the vocabulary is a possible later optimization.

## 7. Upstream quirks to expect in Phase 1

- `docker/Dockerfile.nvidia` never installs `transformers`, which step 1 imports. It
  also installs the DFC wheel with `--no-deps` and a hand-picked dependency list,
  which may be missing more packages. Our image will pin `transformers`.
- Torch isn't pinned either. The `cu124` index resolves to torch 2.6.0, the last
  release built for CUDA 12.4. Step 1 uses the legacy TorchScript exporter
  (`dynamo=False`), which newer torch releases deprecate.
- The `s1` help text gives the defaults as `--seq 24` and `--prefill-size 16`. The
  real defaults are 128 and 32.
- The diagnostics' defaults (scope `ts25mpipe`, embedding quantization parameters,
  `conv49` output) are TinyStories-specific. Pass real values.
- The calibration builder tokenizes each sentence separately, so every sentence gets
  its own BOS, then pads to SEQ with the pad token. That is fine for TinyStories but
  weak for a 512-token Gemma context. Phase 5 needs its own builder.
- The notebooks are stale: they use a 24-token cache and predate the layout fix. Use
  the scripts.
- `docs/status.md` and the wiki disagree about why large models fail. The wiki is
  newer.

## 8. Risk register

| # | Risk | Likelihood | Shows up in | First mitigation |
|---|---|---|---|---|
| 1 | Massive activations wreck 8-bit quantization | high (open upstream for every general-purpose model) | 5, 6 | Measure Gemma's activation ranges in float first. Then find the lossy operations layer by layer in quantized emulation before touching the chip; upstream got prefill emulation running by patching the SDK's cache op. Then try Hailo's 16-bit pieces |
| 2 | The DFC rejects a double-length RoPE table, or calibrates it wrongly | medium | 3 | Test on a 2-layer Gemma-shaped toy before the full model |
| 3 | The parser rejects the tanh-GELU pattern | medium | 3 | Try the erf form, or rewrite tanh-GELU in supported ops |
| 4 | 262k-vocab lm_head: 8 shards of 32768 untested, multi-hour placement, large HEF | medium | 3, 5 | Force an even split (8 × 32768) and check the shard output order. Compile once at a small SEQ first |
| 5 | The compile host runs out of RAM at SEQ 512 | medium | 5 | Do the first iterations at SEQ 128 (also inside the window) |
| 6 | The Pi's HailoRT doesn't match the compiler | high, if the Pi runs Raspberry Pi apt's 5.1.1 | 1, 7 | Install one matching suite drop on the Pi (with your OK) |
| 7 | The cache-inside-window equivalence fails on details | low | 2 | Verify the window rule in Hugging Face's code |
| 8 | Gemma's chat template and stop tokens through hailo-ollama, including BOS on later turns | low to medium | 4, 7 | Compare server-side token ids with Hugging Face's |

## 9. What you need to install or download

See [setup-checklist.md](setup-checklist.md). It also lists the decisions needed
before Phase 1.
