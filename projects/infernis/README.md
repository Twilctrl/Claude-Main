# Infernis

Brand and launch trailer for Infernis, a local AI hosting business.

![The Infernis logo](brand/brand-sheet.png)

## The logo

Both i's in the wordmark are candles. The stem is the letter and the dot is a
crimson flame. The mark is the same candle on its own, so the trailer can open
close on one candle and pull back into the full wordmark.

| File | Use |
| --- | --- |
| `brand/infernis-wordmark.svg` | Wordmark for dark backgrounds |
| `brand/infernis-wordmark-light-bg.svg` | Wordmark for light backgrounds |
| `brand/infernis-wordmark-mono.svg` | One colour, follows `currentColor` |
| `brand/infernis-mark.svg` | The candle, full height |
| `brand/infernis-mark-small.svg` | Short candle for favicons and small sizes |
| `brand/infernis-icon.svg` | App icon (1024 × 1024) |

| Colour | Hex |
| --- | --- |
| Ink | `#0B0A0A` |
| Paper | `#F4F0EC` |
| Flame (crimson) | `#B8122F` |

Type is [Geist](https://github.com/vercel/geist-font) (SIL Open Font License, in
`fonts/`). The wordmark is outlined, so the SVGs don't need the font installed.
To change the logo, edit the constants at the top of `brand/make_brand.py` and run
it (`pip install fonttools brotli`).

## The trailer

`trailer/infernis-trailer.mp4` is the finished trailer: 55 seconds at 1080p and 60 fps, with
sound. `trailer/infernis-trailer.html` is the same trailer as a single page that
plays in any browser, with play, seek and full-screen controls.

The trailer is a prompt box with a local model selected. It types a request, and
each project builds itself out of it:

1. **Cold open.** A glowing `localhost` pill, then the prompt box.
2. **CerberOS.** Three heads (NPU, CPU, remote) wire into one gate on `:11434`,
   then the real wallpaper and a rebuilt start menu fly in as 3D cards.
3. **Feather LLM Keyboard.** Pi, Feather and laptop draw themselves, and data
   pulses along the wires while the LED goes red, green, then blue as a haiku
   types out on the laptop.
4. **Scream and Run.** A heartbeat trace, Onryo walking in, the mod's own phase
   lines ("She's watching." "She's hunting you." "RUN.") and the 5:00 timer.
5. **The four plugins.** prompt-prefix, breadcrumbs, scope-fence and
   session-receipt, one quick cut each.
6. **Code and statement.** The CerberOS gate code in 3D with `localhost:11434`
   circled, then "Your models. Your hardware. Your rules."
7. **The candle.** A spark lights the candle, the camera pulls back into
   `infernis`, an ember lights the second i, and the tagline comes in:
   "AI that never leaves the room."

Everything is drawn from time, frame by frame, so the page and the MP4 match
exactly. The soundtrack is synthesized from the same cue times.

### Rebuild it

Needs Python 3 with numpy, ffmpeg, and Node with Playwright.

```sh
cd projects/infernis/trailer
python3 build.py        # inline fonts, images and the wordmark -> infernis-trailer.html
node cues.cjs           # read keystroke and heartbeat times out of the page
python3 soundtrack.py   # synthesize build/soundtrack.wav and .mp3
python3 build.py        # build again, now with the soundtrack inside
node render.cjs         # render infernis-trailer.mp4
```

`node render.cjs --fps 12 --scale 0.5 --out build/preview.mp4` makes a quick
preview in about a minute. `--from` and `--to` render a section. Scene timings
live in `trailer.src.html`; each `scene(id, start, duration, ...)` call is one
shot.
