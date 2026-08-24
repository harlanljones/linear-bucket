"""Progress calculation, plus recognition of legacy title prefixes.

An earlier version of this sync wrote a zero-padded percentage prefix into
issue titles (``[042%] Fix login``). Linear list views cannot be ordered by
title, so progress moved to labels (see :mod:`parent_progress_sync.buckets`).
The prefix helpers remain so the optional cleanup pass can recognise and strip
titles that version left behind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Width of the zero-padded percentage, chosen so that ``100`` still fits.
PERCENT_WIDTH = 3

#: Matches a prefix this tool previously wrote, including its trailing space.
PREFIX_PATTERN = re.compile(r"^\[(\d{%d})%%\]\s*" % PERCENT_WIDTH)


@dataclass(frozen=True)
class Progress:
    """Completion of a single parent issue."""

    completed: int
    total: int

    @property
    def percent(self) -> int:
        return compute_percent(self.completed, self.total)


def compute_percent(completed: int, total: int) -> int:
    """Return the completion percentage as an integer in ``[0, 100]``.

    Rounding never claims a milestone that has not been reached: a parent with
    outstanding sub-issues never reads ``100%``, and a parent with at least one
    completed sub-issue never reads ``000%``.
    """
    if completed < 0 or total < 0:
        raise ValueError("completed and total must be non-negative")
    if completed > total:
        raise ValueError("completed cannot exceed total")
    if total == 0:
        return 0

    percent = round(completed * 100 / total)
    if percent == 100 and completed < total:
        return 99
    if percent == 0 and completed > 0:
        return 1
    return percent


def strip_prefix(title: str) -> str:
    """Remove a previously applied prefix, leaving other titles untouched."""
    return PREFIX_PATTERN.sub("", title, count=1)


def parse_prefix(title: str) -> int | None:
    """Return the percentage encoded in ``title``, or ``None`` if absent."""
    match = PREFIX_PATTERN.match(title)
    return int(match.group(1)) if match else None
