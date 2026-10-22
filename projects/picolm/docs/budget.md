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
| `.plm` file | 258,654 B | 143,968 B | 143,968 B |
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

| Weights in | Kernel | ms/token | tokens/s (model) | tokens/s with screen |
|---|---|---|---|---|
| PSRAM | viper | 49 | 20 | 16 |
| PSRAM | asm_thumb | 21 | 48 | 37 |
| SD only | viper | 130 | 8 | 6 |
| SD only | asm_thumb | 102 | 10 | 8 |

One token is one character. On a Cortex-M0+ the multiply is a single cycle
and there is no SIMD, so int4 and ternary weights are not faster to compute
than int8; they are smaller, which halves PSRAM traffic and storage. Prompt
characters cost the same as generated ones (minus the output head).

## Quality (measured on the PC)

Filled in from `tools/verify.py` below.
