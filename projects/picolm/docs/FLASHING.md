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

This writes `build/sd/picoware/apps/` (about 288 KB):

```
PicoLM.py                    the app
PicoLM-Test.py               the self-test
picolm_lib/                  engine, ui, tokenizer, hal, selftest (.mpy, compiled for the RP2040)
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

**Library → Applications → Custom → PicoLM.** "loading model..." shows while
the model is copied from the SD card into the PicoCalc's PSRAM (expected
1–2 s), then the menu appears.

### Menu

| Item | What it does |
|---|---|
| New story... | Opens Picoware's keyboard with the last prompt. Edit it and press Enter to start. |
| Again: *prompt* | Starts a new story from the last prompt with the next seed. |
| Temperature | 0.0 (greedy) to 2.0 in steps of 0.1. Default 0.8. |
| Top-k | off, 1, 2, 3, 5, 10, 20 or 40. Default 20. |
| Length | Maximum characters per story: 50 to 1600. Default 400. |
| Seed | The seed for the next story; it goes up by one after each story. |
| Debug overlay | Shows speed, free heap, storage and the exactness check on the status line. |
| Exit | Back to Picoware. |

UP/DOWN move, LEFT/RIGHT change a value (CENTER steps it up), BACK or Esc
leaves the app. Settings and the last prompt are saved in
`picolm_lib/settings.json`.

The prompt editor is Picoware's own keyboard, so it follows Picoware's keys:
Enter starts the story, and the exit key cancels. With Picoware's default
"Exit Button" setting the exit key is Backspace and characters are deleted
with Del; set Settings → Exit Button to Escape if you would rather have
Backspace delete.

### Story view

The prompt appears first, then "prompt *i/n*" on the status line while the
model reads it (one character every ~50–100 ms, estimated), then the story
streams in with word wrap, scrolling half a page when the screen fills. One
token is one character.

| Key | While writing | When stopped or done |
|---|---|---|
| CENTER | stop | start the next story (next seed) |
| UP / DOWN | | scroll back through the story |
| BACK or Esc | back to the menu | back to the menu |

With the debug overlay on, the status line reads, for example,
`14.2t/s 70ms 48K PSRAM golden OK`: tokens per second and milliseconds per
token while generating, free heap in KB, where the weights live (PSRAM, or SD
as a fallback), and the exactness check.

### Exactness check

The default settings (prompt "Once upon a time", temperature 0.8, top-k 20,
seed 1234) match a golden case, so the first "Again" story after installing
is checked: after 200 characters the status line reads **golden OK** if the
PicoCalc produced exactly what the PC reference produces, or
**golden FAIL@n** if it diverged at character n.

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
| RAM use | measured in QEMU: engine code 12.6 KB, engine buffers 23.9 KB; the whole app with its UI about 48 KB, unchanged after a 400-character story |
| UI: menu, settings, prompt entry and cancel, story view, word wrap, scrolling, stop / next story, Esc and BACK, saved settings | scripted against simulated Picoware on CPython, 32-bit MicroPython and QEMU (`tests/test_ui.py`, `device/sim/ui_scenario.py`); about 5 B of heap per generated character, all from finished lines |
| The `.mpy` files from `pip install mpy-cross` (v1.29, .mpy v6.3) | load and pass in QEMU; Picoware's firmware is MicroPython v1.30-preview, which also uses v6.3 |
| Picoware APIs (PSRAM, VFS, Draw, TextBox, input) | checked against Picoware's source; behaviour only on hardware |
| Speed, free heap under Picoware, PSRAM/SD throughput | **hardware only** |
| PSRAM contents surviving Picoware re-initialising the chip | **hardware only**; the app re-checks and reloads if needed |
| Screen layout, fonts, colours, Picoware's keyboard and menu look | **hardware only** (simulated on the PC) |

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
python tests/test_ui.py                          # scripted UI walk-through (CPython)
python device/sim/run_selftest.py models/picolm-s-int8.plm psram 1
python device/sim/run_app.py models/picolm-s-int8.plm   # prints the menu and a story
```

On Linux or WSL, with a MicroPython checkout, the real `viper` code runs on a
32-bit build (the engine refuses 64-bit MicroPython, where 32-bit pointer
loads are not sign-extended):

```sh
make -C mpy-cross
make -C ports/unix CC="gcc -m32" BUILD=build-32 MICROPY_PY_FFI=0 MICROPY_PY_BTREE=0 MICROPY_PY_SSL=0
ports/unix/build-32/micropython -X heapsize=24M device/sim/run_selftest.py models/picolm-s-int8.plm psram
python device/sim/qemu_selftest.py --micropython ~/micropython        # see its docstring for the QEMU build
python device/sim/qemu_selftest.py --micropython ~/micropython --ui   # UI scenarios + heap after a story
```

## Picoware APIs used

Checked against Picoware commit `566d2e5`; if a Picoware update breaks
PicoLM, these are the places to look.

| API | Used in |
|---|---|
| `start(vm)` / `run(vm)` / `stop(vm)` app contract, `vm.back()`, `vm.alert()` | `device/main.py`, `device/selftest_app.py` |
| `vm.draw`: `text`, `clear`, `fill_screen`, `swap`, `font_size`, `size` | `device/picolm_lib/ui.py` |
| `vm.input_manager.button`; `BUTTON_BACK/ESCAPE/CENTER/UP/DOWN/LEFT/RIGHT` | `ui.py`, `device/selftest_app.py` |
| `vm.foreground_color`, `background_color`, `selected_color` | `ui.py` |
| `picoware.gui.menu.Menu`: `add_item`, `clear`, `set_selected`, `scroll_up/down`, `selected_index`, `draw` | `ui.py` |
| `vm.keyboard` (Picoware's Keyboard): `reset`, `title`, `response`, `run(force=, max_characters=)`, `is_finished` | `ui.py` |
| `picoware.system.vector.Vector` (mutable `x`, `y`) | `ui.py` |
| `picoware.gui.textbox.TextBox` | `device/selftest_app.py` |
| `picoware.system.psram.PSRAM`: `size`, `is_ready`, `get_next_free`, `read32_bulk`, `write32_bulk` | `device/picolm_lib/hal.py` |
| builtin `open()` on `/sd/...` (Picoware's VFS): `readinto`, `seek` | `device/picolm_lib/hal.py`, `engine.py` |
