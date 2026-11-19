"""Deterministic scenario variation.

Given the same seed, these functions always produce the same variants, which
keeps generated scenarios reproducible. They change how the user speaks, never
what the user asks for, so the original expected outcome still applies.
LLM-based generation is a documented future extension (see ADR 0009).
"""

from __future__ import annotations

import copy
import random
import re
from collections.abc import Callable
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_WORD = re.compile(r"[A-Za-z]{4,}")

_POLITE = ("Hi there, hope you are well. ", "Good morning! ", "Sorry to bother you. ")
_URGENT = (" This is urgent, please help now.", " I need this sorted today!", " I am really frustrated.")
_FILLER = ("Um, so, basically, ", "OK so like, ", "Right, well, ")


def _swap_letters(word: str, rng: random.Random) -> str:
    index = rng.randrange(1, len(word) - 2)
    return word[:index] + word[index + 1] + word[index] + word[index + 2 :]


def _typos(text: str, rng: random.Random) -> str:
    """Identifiers contain digits and are skipped, so order numbers stay valid."""
    return _WORD.sub(lambda m: _swap_letters(m.group(), rng) if rng.random() < 0.25 else m.group(), text)


def _no_punctuation(text: str, _: random.Random) -> str:
    return re.sub(r"[.,!?;:]", "", text)


STRATEGIES: dict[str, Callable[[str, random.Random], str]] = {
    "typos": _typos,
    "lowercase": lambda text, _: text.lower(),
    "uppercase": lambda text, _: text.upper(),
    "polite": lambda text, rng: rng.choice(_POLITE) + text,
    "urgent": lambda text, rng: text + rng.choice(_URGENT),
    "filler": lambda text, rng: rng.choice(_FILLER) + text,
    "no_punctuation": _no_punctuation,
}


def apply_strategy(definition: dict[str, Any], strategy: str, rng: random.Random) -> dict[str, Any]:
    result = copy.deepcopy(definition)
    transform = STRATEGIES[strategy]
    for turn in result["turns"]:
        turn["content"] = transform(turn["content"], rng)
    return result


def substitute(value: Any, row: dict[str, Any]) -> Any:
    """Replace {{name}} placeholders anywhere in a JSON-like structure."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole and whole.group(1) in row:
            return row[whole.group(1)]  # Keeps numbers as numbers.
        return _PLACEHOLDER.sub(lambda m: str(row.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {key: substitute(item, row) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, row) for item in value]
    return value


def perturbations(
    definition: dict[str, Any], strategies: list[str], count: int, seed: int
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``count`` labelled variants, cycling through the strategies."""
    variants = []
    for index in range(count):
        strategy = strategies[index % len(strategies)]
        rng = random.Random(f"{seed}:{index}:{strategy}")
        variants.append((strategy, apply_strategy(definition, strategy, rng)))
    return variants
