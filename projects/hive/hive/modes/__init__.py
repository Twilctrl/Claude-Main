"""Collaboration modes. Add one by subclassing :class:`Mode` and adding it to ``MODES``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Mode
from .debate import Debate
from .manager import Manager
from .pipeline import Pipeline
from .solo import Solo

if TYPE_CHECKING:
    from ..runtime.run import Run

MODES: dict[str, type[Mode]] = {"pipeline": Pipeline, "debate": Debate, "manager": Manager, "solo": Solo}


def create_mode(name: str, run: "Run") -> Mode:
    try:
        return MODES[name](run)
    except KeyError:
        raise ValueError(f"unknown mode {name!r} (have: {', '.join(MODES)})") from None


__all__ = ["MODES", "Mode", "create_mode"]
