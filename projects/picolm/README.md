# PicoLM

A tiny character-level language model that trains on a PC and runs
integer-only on a [ClockworkPi PicoCalc](https://www.clockworkpi.com/picocalc)
(Raspberry Pi Pico / RP2040, JBlanked's
[Picoware](https://github.com/jblanked/Picoware) MicroPython firmware).
It is an experiment: the model is small and silly, but the goal is that it
genuinely runs on the device.

## Status

| Milestone | State |
|---|---|
| 0. Research, budget, architecture | done: [docs/budget.md](docs/budget.md) |
| 1. PC side: data, tokenizer, model + QAT, export, verifier | **done** |
| 2. Device engine running a hardcoded prompt | next |
| 3. Full Picoware UI | |
| 4. Optimisations (asm_thumb kernel, second core) | |

## How it works

- **Model:** a gated linear recurrent network (minGRU-style: the gates look
  at the input only, so training is parallel over a sequence) with an MLP per
  block and an embedding tied to the output head. The default "S" model has
  d = 128, 2 blocks and 240K weights. Memory per token is constant: the whole
  context is 256 numbers of recurrent state.
- **Integer-only inference:** int4 (or int8 / ternary) weights with per-row
  scales, int8 activations, int32 accumulators, fixed-point requantisation,
  lookup tables for sigmoid, tanh and exp, integer RMSNorm, integer
  temperature/top-k sampling. No floating point on the device. The exact
  arithmetic is specified in [docs/format.md](docs/format.md).
- **On the PicoCalc:** the `.plm` file sits on the SD card, is copied once
  into the PicoCalc's 8 MB PSRAM, and streams through an 8 KB buffer each
  token, so main RAM use stays around 50 KB.

## Results so far (PC side)

S model, 240K weights, trained here on 4 CPU cores in about 47 minutes
(35 float + 12 QAT) on the TinyStories validation split. Held-out bits per
character, measured by `tools/verify.py` with the integer-only engine reading
the exported file:

| Body weights | `.plm` size | float | integer engine |
|---|---|---|---|
| int8 | 259 KB | 1.334 | 1.321 |
| int4 | 144 KB | 1.334 | 1.403 |
| ternary | 144 KB | 1.334 | 1.637 |

The integer engine matches the QAT model's bits/char to within 0.002 and
stays inside the int32 limits the device code assumes. Details, speeds and
caveats are in [docs/budget.md](docs/budget.md).

## Layout

```
train/   data.py (corpus + tokenizer training), tokenizer.py, model.py (PyTorch + QAT),
         train.py, export.py (.plm writer), plm.py (format), fixedpoint.py (integer conventions)
tools/   verify.py, sample.py, budget.py, intref.py (NumPy integer reference = spec for the device)
tests/   test_core.py
docs/    budget.md, format.md
device/  Picoware app (milestone 2+)
```

## Quick start

From this folder (`projects/picolm`), Windows or Linux:

```sh
python -m venv .venv
.venv\Scripts\activate            # Windows (Linux/macOS: source .venv/bin/activate)
pip install numpy
pip install torch --index-url https://download.pytorch.org/whl/cpu   # or a GPU build, see below

python -m train.data --source tinystories-valid --out data/tinystories
python -m train.train --data data/tinystories --preset s --quant int4 --out runs/s-int4
python -m train.export --ckpt runs/s-int4/final.pt --out runs/s-int4/model.plm
python tools/verify.py --plm runs/s-int4/model.plm
python tools/sample.py --plm runs/s-int4/model.plm --prompt "Once upon a time"
python tools/budget.py --plm runs/s-int4/model.plm
python tests/test_core.py
```

`train.train` trains in float first, saves `float.pt`, then switches on
quantisation-aware training and saves `final.pt`. To try another bit width
without retraining the float model:

```sh
python -m train.train --data data/tinystories --init runs/s-int4/float.pt --steps 0 --quant int8 --out runs/s-int8
```

For more data, `--source tinystories-train --max-mb 200` streams the first
200 MB of the large TinyStories file instead of the 19 MB validation split.

## Your own text

`--source` also takes a file or folder of your own text:

- `*.txt` is prose; separate documents with a line `<|endoftext|>`.
- `*.dlg` is dialogue: conversations separated by blank lines, `U:` lines for
  the user, `C:` lines for the character, anything else is narration.

```
U: hello?
C: oh! um... hi.
*she looks at her shoes*
```

`U:` and `C:` become dedicated control tokens, so the device can later prompt
the model as the user and let it answer as the character. To fine-tune on a
small persona corpus, keep the TinyStories vocabulary and start from the
float checkpoint:

```sh
python -m train.data --source my_text/ --out data/mine --tokenizer data/tinystories/tokenizer.json
python -m train.train --data data/mine --init runs/s-int4/float.pt --steps 2000 --quant int4 --out runs/mine
```

## GPU notes (AMD RX 9070 XT, Windows)

The model is small enough that CPU training is fine. Measured here on 4 CPU
cores: about 38,000 tokens/s, so the default run (5,000 float + 1,500 QAT
steps of 64 × 256 tokens, about 106M tokens) took 47 minutes. A desktop CPU
with more cores will be faster.

If you want the GPU: AMD publishes ROCm builds of PyTorch for Windows that
support RDNA4 (gfx1201), but reports of working setups are mixed, so follow
AMD's current install instructions and check `torch.cuda.is_available()`
(ROCm builds report the GPU as "cuda"). `train.train` uses the GPU
automatically when that is true and prints the HIP version; otherwise it uses
the CPU. WSL2 with the Linux ROCm wheels is the more established route.
Nothing here uses `torch.compile` or mixed precision, which are the usual
sources of Windows/ROCm trouble.

## Data licence

TinyStories is released under CDLA-Sharing-1.0, not the public domain. The
scripts download it into `data/` (git-ignored); no corpus text is committed.
