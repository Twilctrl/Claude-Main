"""Core checks for the PC-side pipeline. Run from projects/picolm:

    python tests/test_core.py        (or: python -m pytest tests)
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import torch  # noqa: E402

from tools.intref import IntModel, Sampler  # noqa: E402
from train import fixedpoint as fx  # noqa: E402
from train import plm  # noqa: E402
from train.data import dialogue_docs, normalize  # noqa: E402
from train.export import export_params, residual_scale  # noqa: E402
from train.model import Config, PicoLM  # noqa: E402
from train.tokenizer import CHAR, EOT, USER, Tokenizer, chunks  # noqa: E402


def test_normalize_and_dialogue():
    assert normalize("“Hi” — it’s café…\t") == "\"Hi\" - it's cafe... "
    docs = dialogue_docs("U: hello?\nC: oh! um... hi.\n*waves*\n\nU: bye")
    assert docs == [USER + "hello?\n" + CHAR + "oh! um... hi.\n*waves*\n", USER + "bye\n"]


def test_tokenizer_roundtrip():
    text = "Once upon a time, Lily's cat said \"hi!\"  ok\n" + EOT + "The end. " * 50
    for merges in (0, 30):
        tok = Tokenizer.train(text, max_vocab=96, n_merges=merges)
        ids = tok.encode(text)
        assert tok.decode(ids) == text
        assert tok.encode(EOT) == [0]
        assert len(tok.merges) <= merges
    assert chunks(" cat  " + EOT + " !x") == [" cat", "  ", EOT, " !", "x"]


def test_requant_precision_and_bounds():
    rng = random.Random(0)
    for _ in range(3000):
        m = 10 ** rng.uniform(-6, 3)
        bound = rng.choice([127, 10_000, 300_000, 5_000_000, 200_000_000])
        if bound * m >= 2 ** 27:  # outputs this large cannot be represented anyway
            try:
                fx.requant_params(m, bound)
            except ValueError:
                pass
            continue
        M, s1, sh = fx.requant_params(m, bound)
        assert 1 <= sh <= fx.MAX_SHIFT and 0 <= M < 1 << fx.M_BITS
        if sh < fx.MAX_SHIFT:
            assert abs(M * 2.0 ** -(s1 + sh) / m - 1) < 2 ** -11
        assert abs(fx.rshift(bound, s1)) * M < 1 << 30
        for acc in (bound, -bound, rng.randint(-bound, bound)):
            out = fx.rshift(fx.rshift(acc, s1) * M, sh)
            assert abs(out - acc * m) <= 1 + abs(acc * m) * 2 ** -10 + m * 2 ** s1


def test_rshift_rounds_half_up():
    assert [fx.rshift(v, 1) for v in (-3, -2, -1, 0, 1, 2, 3)] == [-1, -1, 0, 0, 1, 1, 2]
    assert fx.rshift(-5, 0) == -5


def test_pack_roundtrip():
    rng = np.random.default_rng(0)
    for quant, lo, hi in ((plm.Q_INT8, -127, 127), (plm.Q_INT4, -8, 7)):
        q = rng.integers(lo, hi + 1, (7, 16)).astype(np.int8)
        assert np.array_equal(plm.unpack_rows(plm.pack_rows(q, quant), 16, quant), q)


def test_sampler_matches_softmax():
    s = Sampler(7)
    logits = [0, 64, 128, -64, 32]  # frac 6 -> 0, 1, 2, -1, 0.5 nats
    exp_lut = fx.exp_lut()
    counts = np.zeros(5)
    for _ in range(20000):
        counts[s.pick(logits, fx.TEMP_ONE, 0, exp_lut)] += 1
    p = np.exp(np.array(logits) / 64.0)
    p /= p.sum()
    assert np.abs(counts / counts.sum() - p).max() < 0.02
    assert s.pick(logits, 0, 0, exp_lut) == 2
    assert all(s.pick(logits, fx.TEMP_ONE, 2, exp_lut) in (1, 2) for _ in range(200))


def _tiny_model(quant):
    torch.manual_seed(0)
    model = PicoLM(Config(vocab=40, d_model=32, n_layers=2, d_ffn=64, quant=quant))
    model.qat = True
    with torch.no_grad():
        for _ in range(4):
            model(torch.randint(0, 40, (4, 48)))
    model.eval()
    return model


def test_export_and_integer_engine():
    tok = Tokenizer([chr(c) for c in range(32, 32 + 37)])
    assert tok.vocab_size == 40
    for quant in ("int8", "int4", "ternary"):
        model = _tiny_model(quant)
        toks = torch.randint(0, 40, (2, 64), generator=torch.Generator().manual_seed(1))
        P = export_params(model, tok, residual_scale(model, toks))
        Q = plm.read(plm.write(os.devnull, P))
        assert plm.params_equal(P, Q) == []
        im = IntModel(Q, check=True)
        with torch.no_grad():
            ref = model(toks[:1], qat=True)[0][0].numpy()
        got = np.stack([im.step(int(t)) for t in toks[0]]) * 2.0 ** -fx.LOGIT_FRAC
        err = np.abs(got - ref).max()
        assert err < 0.25, (quant, err)
        assert (got.argmax(-1) == ref.argmax(-1)).mean() > 0.95


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
