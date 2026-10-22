# Models

## picolm-s-int8.plm

The default model `tools/build_sd.py` installs.

| | |
|---|---|
| Architecture | gated linear recurrent (minGRU-style) + MLP, 2 blocks, d = 128, FFN 256, tied embedding/head |
| Weights | 240,256; int8 with per-row scales (embedding/head int8 too) |
| Vocabulary | 85 tokens: EOT, USER, CHAR control tokens + 82 printable ASCII characters, no merges |
| Data | TinyStories validation split (CDLA-Sharing-1.0), last 2% of stories held out |
| Training | 5,000 float steps + 1,500 QAT steps, batch 64 × 256, AdamW, 4 CPU cores, 47 minutes |
| Held-out bits/char | 1.334 float, 1.321 integer engine (`tools/verify.py`, 16 × 256 characters) |
| File | 258,656 bytes, `.plm` version 1 ([docs/format.md](../docs/format.md)) |

`picolm-s-int8.golden.json` holds three prompt/settings/seed cases and the
exact token ids the integer reference generates for them
(`tools/golden.py`). The device self-test and the app's status line compare
against it.

Reproduce it with the commands in the main README (`--quant int8`, starting
QAT from the float checkpoint).
