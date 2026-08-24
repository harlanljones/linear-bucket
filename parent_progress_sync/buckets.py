"""The managed set of progress bucket labels.

Linear list views cannot be ordered by title, but they *can* be grouped by
label, so progress is expressed as a label inside one managed group. Bucket
names are zero-padded and numeric-leading, which makes Linear's alphabetical
label ordering match progress order:

    000% not started
    001-024%
    025-049%
    050-074%
    075-099%
    100% complete

Exactly one bucket applies to a parent issue at a time.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_GROUP_NAME = "Parent progress"


@dataclass(frozen=True)
class Bucket:
    """One progress band, inclusive of both bounds."""

    name: str
    low: int
    high: int

    def contains(self, percent: int) -> bool:
        return self.low <= percent <= self.high


#: Ordered from least to most complete. Bands are contiguous and disjoint.
BUCKETS: tuple[Bucket, ...] = (
    Bucket("000% not started", 0, 0),
    Bucket("001-024%", 1, 24),
    Bucket("025-049%", 25, 49),
    Bucket("050-074%", 50, 74),
    Bucket("075-099%", 75, 99),
    Bucket("100% complete", 100, 100),
)

BUCKET_NAMES: tuple[str, ...] = tuple(bucket.name for bucket in BUCKETS)


def bucket_for(percent: int) -> Bucket:
    """Return the single bucket covering ``percent``."""
    for bucket in BUCKETS:
        if bucket.contains(percent):
            return bucket
    raise ValueError(f"no bucket covers {percent}%")
