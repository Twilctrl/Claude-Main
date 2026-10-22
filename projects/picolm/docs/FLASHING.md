# Installing PicoLM on the PicoCalc

PicoLM installs as two Picoware apps plus a library folder on the SD card.
No firmware change is needed.

## What you need

- A PicoCalc with a Raspberry Pi Pico (RP2040) running Picoware's MicroPython
  firmware (`Picoware-PicoCalcPico.uf2`), with the SD card set up as
  [Picoware's installation guide](https://github.com/jblanked/Picoware/blob/main/guides/Installation.md)
  describes (a `picoware` folder at the root of the card).
- A PC with Python 3.10 or newer. PyTorch is not needed for installing.

## 1. Build the SD-card files

From `projects/picolm`:

```sh
pip install mpy-cross
python tools/build_sd.py
```

This writes `build/sd/picoware/apps/` (about 287 KB):

```
PicoLM.py                    the app
PicoLM-Test.py               the self-test
picolm_lib/                  engine, tokenizer, hal, selftest (.mpy, compiled for the RP2040)
picolm_lib/model.plm         the int8 model (models/picolm-s-int8.plm)
picolm_lib/model.golden.json what the model must generate, for the exactness check
```

To install a model you trained yourself, run
`python tools/golden.py --plm runs/<run>/model.plm` and then
`python tools/build_sd.py --model runs/<run>/model.plm`.

## 2. Copy onto the SD card

Power the PicoCalc off, put the SD card in your PC, and copy the `picoware`
folder from `build/sd` onto the root of the card, merging it with the
`picoware` folder already there. You should end up with
`picoware/apps/PicoLM.py` and `picoware/apps/picolm_lib/` on the card. Put
the card back and power on.

## 3. Run PicoLM

**Library → Applications → Custom → PicoLM.**

1. "loading model..." while it copies the model from the SD card into the
   PicoCalc's PSRAM (expected 1–2 s).
2. "thinking (seed 1234)..." while it reads the prompt "Once upon a time"
   (expected about 1 s).
3. The story streams onto the screen. The bottom line shows tokens per second,
   milliseconds per token, free heap in KB, where the weights live (PSRAM, or
   SD as a fallback) and the exactness check. One token is one character.
4. After 200 characters the check reads **golden OK** if the PicoCalc produced
   exactly what the PC reference produces, or **golden FAIL@n** if it diverged
   at character n.

CENTER starts a new story with the next seed; BACK exits. Settings (prompt,
temperature, top-k, length) are constants at the top of `PicoLM.py`.

## 4. Run the self-test and send the numbers back

**Library → Applications → Custom → PicoLM-Test** (expected to take about a
minute). It prints:

```
free heap before load: ... B
copied 258656 bytes to PSRAM in ... ms
loaded in ... ms, weights in PSRAM, engine uses ... B, ... B heap left
golden case 0: MATCH, 120 tokens (... tok/s incl. sampling)
golden case 1: MATCH, 200 tokens ...
golden case 2: MATCH, 100 tokens ...
per token: ... ms (weight reads ..., compute ..., sampling ...) = ... tok/s
heap allocated per token: 0 B
PSRAM read32_bulk: ... MB/s
model file read: ... MB/s
free heap at end: ... B
```

Please send all of it (a photo is fine). These are the numbers
`docs/budget.md` could only estimate: free heap under Picoware, PSRAM and SD
throughput, and the real per-token split between reading weights and
computing. They decide what milestone 4 optimises.

The same report also prints over USB serial, so Thonny or any serial terminal
can capture it as text.

## Verified on a PC vs. only on the PicoCalc

| | Status |
|---|---|
| Engine output equals the PC reference token for token (int8, int4, ternary) | verified: CPython, 32-bit MicroPython, MicroPython in QEMU on an ARM core with RP2040-style (ARMv6-M) code generation |
| No heap allocation while generating | verified (0 B per token) on 32-bit MicroPython and QEMU |
| RAM use | measured in QEMU: engine code 12.6 KB, engine buffers 23.9 KB, whole app about 48 KB |
| The `.mpy` files from `pip install mpy-cross` (v1.29, .mpy v6.3) | load and pass in QEMU; Picoware's firmware is MicroPython v1.30-preview, which also uses v6.3 |
| Picoware APIs (PSRAM, VFS, Draw, TextBox, input) | checked against Picoware's source; behaviour only on hardware |
| Speed, free heap under Picoware, PSRAM/SD throughput | **hardware only** |
| PSRAM contents surviving Picoware re-initialising the chip | **hardware only**; the app re-checks and reloads if needed |
| Screen layout, fonts, key handling | **hardware only** (simulated as a text grid on the PC) |

## Troubleshooting

- **PicoLM is not in the Custom list:** check that `PicoLM.py` is directly in
  `picoware/apps/` and `picolm_lib/` sits next to it.
- **"incompatible .mpy file" or an import error:** your firmware wants a
  different `.mpy` version. Rebuild with `python tools/build_sd.py --no-mpy`
  (plain `.py`, compiled on the device, which needs some extra RAM at
  startup) or with an `mpy-cross` matching your firmware's MicroPython version.
- **MemoryError when starting:** restart the PicoCalc and open PicoLM before
  any other app, then send the self-test's free-heap numbers.
- **"PSRAM unusable ... streaming from SD instead":** it still works, slowly
  (estimated 4–8 tokens/s). Please report the message.
- **golden FAIL or MISMATCH:** the PicoCalc's arithmetic differs from the PC
  reference. Please report the index; that is a bug.
- **Odd screen after exiting:** PicoLM releases PSRAM on exit and Picoware
  re-initialises it; the next screen redraw fixes it.

## Testing without the PicoCalc

From `projects/picolm`:

```sh
python tests/test_device.py                      # engine vs reference, all formats (CPython)
python device/sim/run_selftest.py models/picolm-s-int8.plm psram 1
python device/sim/run_app.py models/picolm-s-int8.plm
```

On Linux or WSL, with a MicroPython checkout, the real `viper` code runs on a
32-bit build (the engine refuses 64-bit MicroPython, where 32-bit pointer
loads are not sign-extended):

```sh
make -C mpy-cross
make -C ports/unix CC="gcc -m32" BUILD=build-32 MICROPY_PY_FFI=0 MICROPY_PY_BTREE=0 MICROPY_PY_SSL=0
ports/unix/build-32/micropython -X heapsize=24M device/sim/run_selftest.py models/picolm-s-int8.plm psram
python device/sim/qemu_selftest.py --micropython ~/micropython   # see its docstring for the QEMU build
```

## Picoware APIs used

Checked against Picoware commit `566d2e5`; if a Picoware update breaks
PicoLM, these are the places to look.

| API | Used in |
|---|---|
| `start(vm)` / `run(vm)` / `stop(vm)` app contract, `vm.back()`, `vm.alert()` | `device/main.py`, `device/selftest_app.py` |
| `vm.draw`: `text`, `clear`, `fill_screen`, `swap`, `font_size`, `size` | `device/main.py` |
| `vm.input_manager`: `button`, `reset()`; `BUTTON_BACK/CENTER/UP/DOWN` | `device/main.py`, `device/selftest_app.py` |
| `picoware.system.vector.Vector` (mutable `x`, `y`) | `device/main.py` |
| `picoware.gui.textbox.TextBox` | `device/selftest_app.py` |
| `picoware.system.psram.PSRAM`: `size`, `is_ready`, `get_next_free`, `read32_bulk`, `write32_bulk` | `device/picolm_lib/hal.py` |
| builtin `open()` on `/sd/...` (Picoware's VFS): `readinto`, `seek` | `device/picolm_lib/hal.py`, `engine.py` |
