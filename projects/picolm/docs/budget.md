# PicoLM budget: what fits on a PicoCalc, and how fast

Target: ClockworkPi PicoCalc with a Raspberry Pi Pico (RP2040), running JBlanked's
Picoware MicroPython firmware. `tools/budget.py` recomputes every number on this
page for any config or exported `.plm`.

Legend: **✅ verified** (read in source or measured), **≈ estimate** (to be
measured on the device in milestone 2), **❓ unknown**.

## Platform facts

From the Picoware source at commit `566d2e5` (2026-10-03) and its shipped
`Picoware-PicoCalcPico.uf2`.

| Item | Value | |
|---|---|---|
| MicroPython | v1.30.0-preview, stock `rp2` port, `BOARD=RPI_PICO` + Picoware C modules | ✅ |
| `@micropython.viper` / `asm_thumb` | compiled in | ✅ |
| `ulab` | not included | ✅ |
| CPU clock | 200 MHz, set by Picoware (`FREQ_RP2040`; 210 MHz was backed out after user reports) | ✅ |
| Flash | 2 MB: firmware 1,475 KB, internal LittleFS 500 KB | ✅ |
| Apps | `/sd/picoware/apps/*.py` or `.mpy`, helper packages in subfolders | ✅ |
| App contract | `start(vm) -> bool`, `run(vm)` called repeatedly and must not block, `stop(vm)` | ✅ |
| SD card | custom FAT32 driver, 25 MHz SPI, no DMA: ≈ 1–2.5 MB/s | ✅ / ≈ |
| PSRAM | 8 MB QSPI via PIO + DMA. Framebuffer (100 KB, RGB332) at address 0, Picoware's allocator above it | ✅ |
| PSRAM reads | `read32_bulk(addr, buf)`: 64-byte bursts at 100 MHz SCK, ≈ 8–20 MB/s. `read_into`/`write`: one transaction per byte, avoid | ✅ / ≈ |
| Display | full-screen `Draw.swap()` only from Python: 205 KB over 75 MHz SPI + 100 KB PSRAM read ≈ 28 ms. Text costs one PSRAM write per lit pixel ≈ 40 µs/glyph | ✅ / ≈ |
| Free heap for an app | not documented; stock MicroPython on RP2040 has ~155–190 KB, Picoware's UI takes part of it. Planning value 70–120 KB, fragmented | ❓ |
| Second core | `_thread` available; Picoware only uses core 1 on demand (time sync, etc.) | ✅ |

Overclocking past 200 MHz is not counted anywhere: the LCD and PSRAM PIO
dividers are computed at init from the clock, so a runtime jump to 250 MHz
would run the LCD SPI near 94 MHz and PSRAM SCK at 125 MHz.

## Model: PicoLM-S (default)

Character-level, gated linear recurrent (minGRU-style) with an MLP per block.
85-token vocab (3 control tokens + 82 characters on TinyStories), d = 128,
2 blocks, FFN 256, embedding tied with the output head: **240,256 weights**.

| | int8 body | int4 body | ternary (int4 container) |
|---|---|---|---|
| `.plm` file | 258,656 B | 143,968 B | 143,968 B |
| weight records streamed per token | 253,904 B | 139,216 B | 139,216 B |
| multiply-adds per token | 240,256 | 240,256 | 240,256 |

Each row carries 8 bytes of requantisation constants, which is why int4 records
are 139 KB rather than 120 KB.

## Main RAM (SRAM)

Weights live in PSRAM and stream through a staging buffer; nothing else large
is resident.

| Item | KB |
|---|---|
| weight staging buffer | 8.0 |
| activations, recurrent state, logits | 4.7 |
| LUTs (sigmoid, tanh, exp) | 3.0 |
| norm gains | 1.3 |
| tokenizer tables | 0.2 |
| code: engine + UI (≈) | 30.0 |
| UI text buffer | 2.0 |
| **total** | **≈ 49** |

Keeping all int4 weights resident in SRAM instead would need ≈ 177 KB, which
does not fit next to Picoware.

## Speed (all ≈ until measured)

Assumptions: 200 MHz; ≈ 30 cycles per multiply-add in `viper`, ≈ 6.5 in a
hand-written `asm_thumb` kernel (unrolled `ldrb`/`ldr`/`muls`/`adds`, with
offset-binary weights so nothing needs sign extension); PSRAM 12 MB/s; SD
1.5 MB/s; 1.5 ms per token of glue; screen refreshed 8 times a second at
28 ms each.

| Weights in | Kernel | int4: ms/token | int4: tokens/s with screen | int8: ms/token | int8: tokens/s with screen |
|---|---|---|---|---|---|
| PSRAM | viper | 49 | 16 | 59 | 13 |
| PSRAM | asm_thumb | 21 | 37 | 31 | 26 |
| SD only | viper | 130 | 6 | 207 | 4 |
| SD only | asm_thumb | 102 | 8 | 179 | 4 |

One token is one character. On a Cortex-M0+ the multiply is a single cycle
and there is no SIMD, so int4 and ternary weights are not faster to compute
than int8; they are smaller, which halves PSRAM traffic and storage. Prompt
characters cost the same as generated ones (minus the output head).

## Quality (measured on the PC)

S model, character-level, trained on the TinyStories validation split
(18.7M training characters, last 2% of stories held out): 5,000 float steps,
then 1,500 QAT steps per bit width from the same float checkpoint, batch
64 × 256. Bits per character from `tools/verify.py` on 16 × 256 held-out
characters:

| Body weights | float model (before QAT) | QAT model | integer engine | integer vs QAT: max / mean logit error, argmax agreement |
|---|---|---|---|---|
| int8 | 1.334 | 1.322 | 1.321 | 1.14 / 0.11 nat, 97.4% |
| int4 | 1.334 | 1.403 | 1.403 | 0.86 / 0.09 nat, 97.9% |
| ternary | 1.334 | 1.635 | 1.637 | 0.61 / 0.07 nat, 97.9% |

The integer engine reproduces the QAT model's quality exactly (bits/char equal
to within 0.002). Its logits are not bit-identical to PyTorch: stage by stage,
every integer operation lands within one rounding step of its float
counterpart, and those 1-step differences in the int8 activations add up to a
mean logit difference of about 0.1 nat. Every intermediate stays inside the
int32 bounds the device code relies on; the largest, the state update
`z * (cand - h)`, reaches 2^30.0, which is its designed maximum.

int4 costs about 0.08 bits/char against int8 and saves 45% of the bytes
streamed per token. Ternary costs 0.3 bits/char and, stored in the int4
container, saves nothing, so it is not worth using at this model size.

Samples (int4, temperature 0.7, top-k 20), which is the expected level for
240K weights trained for under an hour on a CPU:

> Once upon a time, there was a big boy named Tim. Tim loved to play with it
> with his friends and landess in the backyard. One day, Jack was family. Tim
> loved to watch the park with his mom and it started to play on the sky.

More data (`--source tinystories-train --max-mb 200`) and longer training are
the cheapest quality gains; neither changes the device budget.
