"""Device code vs the PC reference, run on CPython through device/sim/shim.py.

    python tests/test_device.py        (or: python -m pytest tests)

The engine's integer logits must equal tools/intref.py's exactly, for every
weight format; the device tokenizer and PRNG must match the PC versions.
Real viper code is exercised by device/sim/run_selftest.py on MicroPython.
"""

import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "device", "sim"))
sys.path.insert(0, os.path.join(ROOT, "device"))

import shim  # noqa: E402

shim.install()

import numpy as np  # noqa: E402
import torch  # noqa: E402

from picolm_lib import tokenizer as dev_tok  # noqa: E402
from picolm_lib.engine import Engine  # noqa: E402
from tools.intref import IntModel, Sampler  # noqa: E402
from train import plm  # noqa: E402
from train.export import export_params, residual_scale  # noqa: E402
from train.model import Config, PicoLM  # noqa: E402
from train.tokenizer import EOT, Tokenizer  # noqa: E402

TEXT = ("Once upon a time, Lily's cat said \"hi!\"  ok\n" + EOT + "The dog ran. " * 40
        + "\x01hello?\n\x02oh! um... hi.\n")


def _tiny_plm(quant, tok, path):
    torch.manual_seed(3)
    model = PicoLM(Config(vocab=tok.vocab_size, d_model=32, n_layers=2, d_ffn=64, quant=quant))
    model.qat = True
    with torch.no_grad():
        for _ in range(3):
            model(torch.randint(0, tok.vocab_size, (4, 40)))
    model.eval()
    toks = torch.randint(0, tok.vocab_size, (2, 48), generator=torch.Generator().manual_seed(4))
    P = export_params(model, tok, residual_scale(model, toks))
    plm.write(path, P)
    return P


def test_engine_logits_exact_all_formats():
    tok = Tokenizer.train(TEXT, max_vocab=48, n_merges=6)
    seq = [0] + tok.encode("Once upon a time, the dog said hi.")
    with tempfile.TemporaryDirectory() as tmp:
        for quant in ("int8", "int4", "ternary"):
            path = os.path.join(tmp, quant + ".plm")
            P = _tiny_plm(quant, tok, path)
            ref = IntModel(plm.read(path), check=True)
            for mode in ("ram", "sd"):
                eng = Engine(path, mode)
                eng.reset()
                ref.reset()
                for t in seq:
                    want = ref.step(t)
                    eng.step(t)
                    assert list(eng.logits) == [int(v) for v in want], (quant, mode, t)
                eng.close()
            assert P["header"]["quant_body"] == (plm.Q_INT8 if quant == "int8" else plm.Q_INT4)


def test_psram_recovery():
    from picoware.system import psram as fake

    tok = Tokenizer.train(TEXT, max_vocab=48)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.plm")
        _tiny_plm("int4", tok, path)
        ref = IntModel(plm.read(path))
        eng = Engine(path, "psram")
        assert eng.mode == "PSRAM" and eng.ensure_loaded() is False
        fake.simulate_deinit()            # interface shut down, data kept: re-init only
        assert eng.ensure_loaded() is False
        fake.simulate_wipe()              # data lost: reload from the file
        fake.simulate_deinit()
        assert eng.ensure_loaded() is True
        eng.reset()
        for t in [0, 5, 9, 12]:
            eng.step(t)
            assert list(eng.logits) == [int(v) for v in ref.step(t)]
        eng.close()


def test_tokenizer_matches_pc():
    for merges in (0, 25):
        pc = Tokenizer.train(TEXT, max_vocab=96, n_merges=merges)
        dev = dev_tok.Tokenizer(list(pc.tokens), len(pc.merges), list(pc.merges), pc.unk_id)
        rng = random.Random(merges)
        for _ in range(50):
            s = "".join(rng.choice("abc dog Lily's \"!?\n.\x01\x02\x03~") for _ in range(rng.randint(0, 40)))
            assert dev.encode(s) == pc.encode(s), s
            assert dev.decode(dev.encode(s)) == pc.decode(pc.encode(s))


def test_prng_matches_reference():
    with tempfile.TemporaryDirectory() as tmp:
        tok = Tokenizer.train(TEXT, max_vocab=48)
        path = os.path.join(tmp, "m.plm")
        _tiny_plm("int8", tok, path)
        eng = Engine(path, "ram")
        for seed in (0, 1, 1234, 0xFFFFFFFF, 2 ** 31 + 7):
            ref = Sampler(seed)
            eng.seed(seed)
            assert [eng._rand30() for _ in range(500)] == [ref.rand30() for _ in range(500)], seed
        eng.close()


def test_sampling_matches_reference():
    with tempfile.TemporaryDirectory() as tmp:
        tok = Tokenizer.train(TEXT, max_vocab=48)
        path = os.path.join(tmp, "m.plm")
        _tiny_plm("int4", tok, path)
        eng = Engine(path, "ram")
        ref = IntModel(plm.read(path))
        rng = np.random.default_rng(0)
        for temp, k in ((0, 0), (256, 0), (205, 20), (16, 3), (1024, 5)):
            eng.seed(99)
            s = Sampler(99)
            for _ in range(30):
                logits = rng.integers(-2000, 2000, eng.V)
                for i, v in enumerate(logits):
                    eng.logits[i] = int(v)
                assert eng.sample(temp, k) == s.pick(logits, temp, k, ref.exp), (temp, k)
        eng.close()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {e!r}")
    sys.exit(1 if failed else 0)
