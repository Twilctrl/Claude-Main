# code.py -- Adafruit Feather RP2350: UART-to-USB-keyboard bridge (CircuitPython)
#
# A Raspberry Pi running a local LLM sends newline-terminated commands over
# UART. The Feather "types" them into the laptop it is plugged into over USB.
#
#   Raspberry Pi  --UART-->  Feather RP2350  --USB HID keyboard-->  Laptop
#
# Wiring (both boards are 3.3 V logic, so connect directly):
#   Pi GPIO14 / TXD (pin 8)   -> Feather RX
#   Pi GPIO15 / RXD (pin 10)  <- Feather TX
#   Pi GND          (pin 6)   -- Feather GND
#
# Protocol: one command per line (UTF-8, terminated by "\n"). The Feather
# answers every command with exactly one line, "OK ..." or "ERR ...", so the
# Pi should wait for that reply before sending the next command. It may also
# send unsolicited "EVT ..." lines (e.g. when the arm button is pressed).
#
#   PING                 -> OK PONG
#   STATUS               -> OK armed=<0|1> cps=<n>
#   TYPE <text>          type text; escapes: \n (Enter), \t (Tab), \\ (backslash)
#   KEY <combo> [...]    press combos, e.g. "KEY CTRL+C", "KEY ALT+TAB ENTER"
#   WAIT <ms>            pause (max 10000 ms)
#   SPEED <cps>          set typing speed in characters per second (1-200)
#   RELEASE              release every key
#   DISARM               stop accepting TYPE/KEY until the button is pressed
#
# Safety: the Feather boots DISARMED and ignores TYPE/KEY until you press the
# arm button. Pressing it again (even mid-sentence) disarms immediately and
# aborts whatever is being typed. The Pi can disarm but can never arm, so a
# misbehaving model can't take over the keyboard without a human in the loop.
#
# Required libraries (copy from the CircuitPython bundle into CIRCUITPY/lib):
#   adafruit_hid/   neopixel.mpy (optional, for the status LED)

import time

import board
import busio
import digitalio
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.keycode import Keycode

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
UART_BAUD = 115200
DEFAULT_CPS = 40          # typing speed, characters per second
MAX_CPS = 200
MAX_LINE = 1024           # longest command line accepted, in bytes
MAX_WAIT_MS = 10000
START_ARMED = False       # True skips the button press at power-up (not advised)
DEBOUNCE_S = 0.05

# The Feather's on-board BOOT button, if this CircuitPython build exposes it.
# Otherwise wire a momentary pushbutton between D5 and GND.
ARM_BUTTON_PIN = getattr(board, "BUTTON", None) or board.D5

# Typographic characters LLMs love that a US keyboard can't type directly.
TRANSLITERATE = {
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "--", "−": "-", "‐": "-", "‑": "-",
    "…": "...", "•": "*", "·": "*",
    " ": " ", " ": " ", " ": " ", "​": "",
    "×": "x", "→": "->", "←": "<-", "≤": "<=", "≥": ">=",
}

ESCAPES = {"n": "\n", "t": "\t", "r": "", "\\": "\\"}

KEY_ALIASES = {
    "CTRL": "CONTROL", "LCTRL": "LEFT_CONTROL", "RCTRL": "RIGHT_CONTROL",
    "CMD": "GUI", "WIN": "GUI", "SUPER": "GUI", "META": "GUI",
    "OPT": "ALT", "OPTION": "ALT",
    "ESC": "ESCAPE", "DEL": "DELETE", "INS": "INSERT", "BKSP": "BACKSPACE",
    "SPACE": "SPACEBAR", "RETURN": "ENTER",
    "PGUP": "PAGE_UP", "PGDN": "PAGE_DOWN",
    "UP": "UP_ARROW", "DOWN": "DOWN_ARROW",
    "LEFT": "LEFT_ARROW", "RIGHT": "RIGHT_ARROW",
}
DIGIT_NAMES = ("ZERO", "ONE", "TWO", "THREE", "FOUR",
               "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")

# Status LED colours
RED = (40, 0, 0)       # disarmed
GREEN = (0, 40, 0)     # armed, idle
BLUE = (0, 0, 60)      # typing
YELLOW = (40, 30, 0)   # error

# ---------------------------------------------------------------------------
# Hardware setup
# ---------------------------------------------------------------------------
try:
    import neopixel
    pixel = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.3, auto_write=True)
except (ImportError, AttributeError):
    pixel = None

button = digitalio.DigitalInOut(ARM_BUTTON_PIN)
button.switch_to_input(pull=digitalio.Pull.UP)   # pressed == low

uart = busio.UART(board.TX, board.RX, baudrate=UART_BAUD, timeout=0,
                  receiver_buffer_size=4096)

# Give the host a moment to enumerate the keyboard before we use it.
time.sleep(1)
while True:
    try:
        keyboard = Keyboard(usb_hid.devices)
        break
    except OSError:
        time.sleep(0.5)
layout = KeyboardLayoutUS(keyboard)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
armed = START_ARMED
cps = DEFAULT_CPS
_last_button = button.value
_last_button_change = 0.0


class Disarmed(Exception):
    pass


def set_led(color):
    if pixel is not None:
        pixel[0] = color


def idle_led():
    set_led(GREEN if armed else RED)


def reply(line):
    uart.write((line + "\n").encode("utf-8"))


def poll_button():
    """Toggle armed state on each debounced press. Returns True if toggled."""
    global armed, _last_button, _last_button_change
    value = button.value
    now = time.monotonic()
    if value == _last_button or now - _last_button_change < DEBOUNCE_S:
        return False
    _last_button = value
    _last_button_change = now
    if value:  # released
        return False
    armed = not armed
    if not armed:
        keyboard.release_all()
    idle_led()
    reply("EVT ARMED" if armed else "EVT DISARMED")
    return True


def check_armed():
    """Called between keystrokes so a button press aborts typing at once."""
    poll_button()
    if not armed:
        keyboard.release_all()
        raise Disarmed()


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------
def unescape(s):
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            out.append(ESCAPES.get(nxt, "\\" + nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def type_text(text):
    """Type text one character at a time. Returns count of skipped chars."""
    delay = 1.0 / cps
    skipped = 0
    for ch in text:
        ch = TRANSLITERATE.get(ch, ch)
        for c in ch:
            check_armed()
            try:
                codes = layout.keycodes(c)
            except ValueError:
                skipped += 1
                continue
            keyboard.press(*codes)
            keyboard.release_all()
            time.sleep(delay)
    return skipped


def parse_combo(token):
    codes = []
    for part in token.upper().split("+"):
        if not part:
            raise ValueError("empty key in " + token)
        name = KEY_ALIASES.get(part, part)
        if len(name) == 1 and name in "0123456789":
            name = DIGIT_NAMES[int(name)]
        code = getattr(Keycode, name, None)
        if not isinstance(code, int):
            raise ValueError("unknown key " + part)
        codes.append(code)
    return codes


def press_combos(arg):
    combos = [parse_combo(tok) for tok in arg.split()]   # validate all first
    if not combos:
        raise ValueError("no keys given")
    for codes in combos:
        check_armed()
        keyboard.press(*codes)
        time.sleep(0.02)
        keyboard.release_all()
        time.sleep(max(1.0 / cps, 0.02))


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------
def handle(line):
    global cps, armed
    cmd, _, arg = line.partition(" ")
    cmd = cmd.upper()

    if cmd == "PING":
        return "OK PONG"
    if cmd == "STATUS":
        return "OK armed=%d cps=%d" % (1 if armed else 0, cps)
    if cmd == "RELEASE":
        keyboard.release_all()
        return "OK"
    if cmd == "DISARM":
        if armed:
            armed = False
            keyboard.release_all()
            idle_led()
        return "OK"
    if cmd == "SPEED":
        cps = max(1, min(MAX_CPS, int(arg)))
        return "OK cps=%d" % cps
    if cmd == "WAIT":
        end = time.monotonic() + max(0, min(MAX_WAIT_MS, int(arg))) / 1000
        while time.monotonic() < end:
            poll_button()
            time.sleep(0.01)
        return "OK"

    if cmd in ("TYPE", "KEY"):
        if not armed:
            return "ERR DISARMED"
        set_led(BLUE)
        try:
            if cmd == "TYPE":
                skipped = type_text(unescape(arg))
                return "OK skipped=%d" % skipped if skipped else "OK"
            press_combos(arg)
            return "OK"
        except Disarmed:
            return "ERR DISARMED"
        finally:
            keyboard.release_all()
            idle_led()

    return "ERR unknown command " + cmd


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    buf = bytearray()
    overflow = False
    idle_led()
    reply("EVT READY armed=%d" % (1 if armed else 0))

    while True:
        poll_button()

        n = uart.in_waiting
        if not n:
            time.sleep(0.002)
            continue

        for b in uart.read(n):
            if b == 0x0D:          # ignore CR
                continue
            if b != 0x0A:          # not end of line yet
                if len(buf) < MAX_LINE:
                    buf.append(b)
                else:
                    overflow = True
                continue

            if overflow:
                response = "ERR line too long (max %d bytes)" % MAX_LINE
            elif not buf:
                buf = bytearray()
                continue
            else:
                try:
                    response = handle(bytes(buf).decode("utf-8"))
                except UnicodeError:
                    response = "ERR invalid UTF-8"
                except ValueError as e:
                    response = "ERR " + str(e)
                except Exception as e:  # never let one bad command kill the bridge
                    keyboard.release_all()
                    set_led(YELLOW)
                    response = "ERR %s: %s" % (type(e).__name__, e)
            buf = bytearray()
            overflow = False
            reply(response)


main()
