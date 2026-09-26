# Feather RP2350 LLM Keyboard

A Raspberry Pi runs a local LLM. An Adafruit Feather RP2350 takes the model's
output from the Pi over UART and types it into a laptop as a normal USB keyboard.
The laptop needs no drivers or software.

```
 Raspberry Pi (Ollama + llm_bridge.py) --UART--> Feather RP2350 --USB HID--> Laptop
```

## Wiring

Both boards use 3.3 V logic, so the wires connect directly with no level shifter.

| Raspberry Pi            | Feather RP2350 |
| ----------------------- | -------------- |
| GPIO14 / TXD (pin 8)    | RX             |
| GPIO15 / RXD (pin 10)   | TX             |
| GND (pin 6)             | GND            |

The Feather is powered from the laptop's USB. Don't connect the two 3V/5V rails
to each other.

**Arm button:** the firmware uses the Feather's on-board BOOT button when
CircuitPython exposes it as `board.BUTTON`. If your build doesn't, wire a
pushbutton between **D5** and **GND**.

## Feather setup

1. Install CircuitPython for the Feather RP2350 (circuitpython.org/board/adafruit_feather_rp2350).
2. From the matching CircuitPython library bundle, copy these to `CIRCUITPY/lib/`:
   - `adafruit_hid/`
   - `neopixel.mpy` (optional; drives the status LED)
3. Copy `feather/boot.py` and `feather/code.py` to the root of `CIRCUITPY`.
4. Press RESET. `boot.py` changes only take effect after a hard reset.

### Status LED

| Colour | Meaning                                      |
| ------ | -------------------------------------------- |
| Red    | Disarmed. Keystrokes from the Pi are refused |
| Green  | Armed and idle                               |
| Blue   | Typing                                       |
| Yellow | The last command caused an error             |

### Safety model

- The Feather starts **disarmed**. Press the arm button to let it type.
- Press the button again at any time, including mid-sentence, to stop typing
  and release every key immediately.
- The Pi can send `DISARM` but has no command to arm. Only a person pressing
  the button can arm it, so a misbehaving model can't take over the laptop's
  keyboard by itself.

## Pi setup

1. Enable the UART: `sudo raspi-config` → Interface Options → Serial Port →
   login shell over serial: **No**, serial port hardware: **Yes**. Then reboot.
2. Install and start [Ollama](https://ollama.com), then pull a small model, e.g.
   `ollama pull llama3.2:3b`.
3. `pip install pyserial` (or `sudo apt install python3-serial`).
4. Run the bridge:

```sh
python3 pi/llm_bridge.py                          # interactive prompt loop
python3 pi/llm_bridge.py "Write a haiku about USB"  # one-shot
python3 pi/llm_bridge.py --cps 80 --enter --model qwen2.5:1.5b
```

Press the arm button on the Feather, click into a text field on the laptop, and
enter a prompt on the Pi.

## Serial protocol

The protocol is plain text at 115200 baud 8N1, with one command per line ending
in `\n`. The Feather sends exactly one `OK …` or `ERR …` reply to each command,
so wait for that reply before sending the next command. This gives you flow
control for free. The Feather may also send unprompted `EVT …` lines:
`EVT READY`, `EVT ARMED` and `EVT DISARMED`.

| Command              | Effect                                                         |
| -------------------- | -------------------------------------------------------------- |
| `PING`               | Replies `OK PONG`                                              |
| `STATUS`             | Replies `OK armed=<0/1> cps=<n>`                               |
| `TYPE <text>`        | Types the text. Escapes: `\n` Enter, `\t` Tab, `\\` backslash  |
| `KEY <combo> [...]`  | Presses key combos, e.g. `KEY CTRL+C` or `KEY ALT+TAB ENTER`   |
| `WAIT <ms>`          | Pauses for up to 10000 ms                                      |
| `SPEED <cps>`        | Sets typing speed, 1–200 characters per second                 |
| `RELEASE`            | Releases all keys                                              |
| `DISARM`             | Disarms. Re-arm with the physical button                       |

Everything after the first space in `TYPE` is typed as-is, including leading
spaces. That matters because LLM tokens usually start with a space.

Characters a US keyboard can't type are handled in one of two ways. Common
typographic characters are converted first: curly quotes become straight
quotes, `—` becomes `--`, `…` becomes `...`, and so on. Anything still left is
skipped and counted in the reply, e.g. `OK skipped=2`.

Key names are the names from
[`adafruit_hid.keycode.Keycode`](https://docs.circuitpython.org/projects/hid/en/latest/api.html#adafruit-hid-keycode-keycode),
plus these aliases: `CTRL`, `CMD`/`WIN`/`SUPER`, `OPT`, `ESC`, `DEL`, `BKSP`,
`SPACE`, `RETURN`, `PGUP`/`PGDN` and `UP`/`DOWN`/`LEFT`/`RIGHT`.

To test without the LLM, open a terminal on the Pi with
`python3 -m serial.tools.miniterm /dev/serial0 115200` and type commands by hand.

## Notes

- The firmware assumes the laptop uses a **US keyboard layout**. On other
  layouts, swap `KeyboardLayoutUS` for a layout module from
  `circuitpython_keyboard_layout_*`.
- USB HID typing is slower than most LLMs generate text. The bridge prints
  tokens on the Pi as they arrive, and the Feather's `OK` replies stop the Pi
  from sending faster than the Feather can type. Raise `--cps` if the laptop
  keeps up. Some apps drop keys above roughly 100 cps.
