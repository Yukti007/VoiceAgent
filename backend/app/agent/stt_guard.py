"""Detection (log-only) for a known Sarvam Saaras realtime STT failure mode:
on some short/quiet audio segments it repeats a single word dozens of times
instead of transcribing real speech (e.g. "हाँ हाँ हाँ..." x100).

This module only flags the pattern for visibility -- see
Assistant.on_user_turn_completed in app/agent/agent.py. It deliberately does
not discard or rewrite the transcript: it was observed once during manual
testing, not yet confirmed as a recurring pattern, and a discard-and-reprompt
guard tuned on a single instance risks false-positiving on legitimate
repeated words (e.g. "haan haan haan" is normal Hindi speech). Once real
call logs show how often this fires and at what repeat counts, that data
should drive whether/how to act on it instead of just logging it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class RepetitionSignal:
    """Diagnostic info about a transcript dominated by one repeated token."""

    token: str
    count: int
    total_tokens: int
    ratio: float


def detect_repetition(
    text: str, *, min_repeats: int = 6, min_ratio: float = 0.6
) -> RepetitionSignal | None:
    """Flags a transcript where one token accounts for most of its words.

    Both thresholds must be met: `min_repeats` guards against false positives
    on short normal utterances (e.g. a single "haan"), and `min_ratio` guards
    against longer utterances that happen to reuse a filler word a few times.
    Returns None for anything that doesn't look degenerate.
    """
    tokens = text.split()
    if not tokens:
        return None

    token, count = Counter(tokens).most_common(1)[0]
    ratio = count / len(tokens)
    if count >= min_repeats and ratio >= min_ratio:
        return RepetitionSignal(token=token, count=count, total_tokens=len(tokens), ratio=ratio)
    return None
