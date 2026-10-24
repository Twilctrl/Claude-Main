"""Fitting history into small context windows."""

from hive.backends import ChatMessage, ToolCall
from hive.context import Estimator, clip, compact, fit


def msgs(n, size=100):
    out = []
    for i in range(n):
        out.append(ChatMessage("user", f"q{i} " + "x" * size))
        out.append(ChatMessage("assistant", f"a{i} " + "y" * size))
    return out


def test_fit_keeps_newest_and_system():
    est = Estimator(4.0)
    history = msgs(20)
    sent, dropped = fit("SYSTEM", history, budget=200, est=est)
    assert sent[0].role == "system" and sent[0].content == "SYSTEM"
    assert sent[-1].content.startswith("a19")
    assert dropped > 0 and len(sent) - 1 + dropped == len(history)


def test_fit_never_starts_with_orphan_tool_result():
    est = Estimator(4.0)
    history = [
        ChatMessage("user", "go " + "x" * 400),
        ChatMessage("assistant", "", tool_calls=[ToolCall("read_file", {"path": "a"}, "c1")]),
        ChatMessage("tool", "data " * 50, tool_call_id="c1", name="read_file"),
        ChatMessage("assistant", "done"),
    ]
    sent, _ = fit("S", history, budget=120, est=est)
    assert sent[1].role != "tool"
    roles = [m.role for m in sent]
    if "tool" in roles:
        assert roles[roles.index("tool") - 1] == "assistant"


def test_fit_clips_a_single_huge_turn():
    est = Estimator(4.0)
    sent, _ = fit("S", [ChatMessage("user", "z" * 50_000)], budget=500, est=est)
    assert len(sent) == 2 and len(sent[1].content) < 3000 and "cut" in sent[1].content


def test_clip_keeps_head_and_tail():
    text = "HEAD" + "m" * 1000 + "TAIL"
    c = clip(text, 200)
    assert c.startswith("HEAD") and c.endswith("TAIL") and len(c) < 260


async def test_compact_summarizes_when_budget_allows():
    est = Estimator(4.0)
    history = msgs(40, size=200)
    seen = {}

    async def summarize(old, transcript, max_tokens):
        seen["transcript"] = transcript
        return "SUMMARY: talked about q0..q30"

    new, summary, how = await compact("S", history, "", budget=4000, est=est, summarize=summarize)
    assert how == "summarized" and summary.startswith("SUMMARY")
    assert len(new) < len(history) and new[-1] == history[-1]
    assert "q0" in seen["transcript"]


async def test_compact_truncates_on_tiny_budget_or_failure():
    est = Estimator(4.0)
    history = msgs(40, size=200)

    async def boom(*a):
        raise RuntimeError("model fell over")

    new, summary, how = await compact("S", history, "old", budget=1500, est=est, summarize=boom)
    assert how == "truncated" and summary == "old" and len(new) < len(history)
    new, summary, how = await compact("S", history, "old", budget=3000, est=est, summarize=boom)
    assert how == "truncated" and summary == "old"


async def test_compact_noop_when_it_fits():
    est = Estimator(4.0)
    history = msgs(2)
    new, summary, how = await compact("S", history, "", budget=4000, est=est, summarize=None)
    assert how == "none" and new == history


def test_estimator_calibrates():
    est = Estimator(3.5)
    for _ in range(20):
        est.calibrate(4000, 1000)
    assert 3.9 < est.chars_per_token <= 4.0
    est.calibrate(4000, 10)  # implausible: ignored
    assert est.chars_per_token > 3.9
