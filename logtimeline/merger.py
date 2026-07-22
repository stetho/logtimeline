"""
Merges LogEntry streams from multiple sources into a single time-ordered
stream.

Handles the case parser.py can produce: a source's earliest entries might
have timestamp=None (e.g. a startup banner printed before the first line
the source actually stamps). Rather than dropping these or dumping them
all at the start of the merged output, they're anchored to the nearest
real timestamp in their own source - forward-filled from the next real
timestamp if they're a leading run, backward-filled from the last real
timestamp if (unusually) they appear after one. This keeps a source's
internal read order intact even where we don't know its real time.

If an entire source never produces a single real timestamp, there's no
time reference to anchor it to at all - those entries are placed at the
end of the merged output, in original order, and flagged via
MergedEntry.untimestamped_source so a caller (e.g. the CLI) can warn
about it rather than the ambiguity being silently swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator

from .parser import LogEntry


@dataclass
class MergedEntry:
    entry: LogEntry
    effective_timestamp: datetime | None
    untimestamped_source: bool  # True if this entry's source had no real timestamp at all


def _fill_effective_timestamps(entries: list[LogEntry]) -> list[datetime | None]:
    n = len(entries)
    effective: list[datetime | None] = [None] * n

    # Backward-fill: each position gets the most recent real timestamp
    # seen so far in this source.
    last_real: datetime | None = None
    for i, e in enumerate(entries):
        if e.timestamp is not None:
            last_real = e.timestamp
        effective[i] = last_real

    # Forward-fill: any leading run still None (before the first real
    # timestamp ever appears) picks up that first real timestamp instead.
    next_real: datetime | None = None
    for i in range(n - 1, -1, -1):
        if effective[i] is not None:
            next_real = effective[i]
        elif next_real is not None:
            effective[i] = next_real

    return effective


def merge_sources(
    sources: dict[str, Iterable[LogEntry]]
) -> Iterator[MergedEntry]:
    """Merge named LogEntry streams into one time-ordered stream.

    `sources` maps a source label to an iterable of LogEntry (e.g. the
    output of parser.parse_file()). Each source is fully materialised to
    allow the anchoring look-ahead/behind described above - fine for
    typical log volumes; very large sources are a known v1 limitation.
    """
    timestamped: list[tuple[datetime, int, int, LogEntry]] = []
    untimestamped: list[tuple[int, int, LogEntry]] = []

    for source_order, (source_name, entry_iter) in enumerate(sources.items()):
        entries = list(entry_iter)
        if not entries:
            continue

        effective = _fill_effective_timestamps(entries)

        if effective[0] is None and all(ts is None for ts in effective):
            # This source never produced a single real timestamp anywhere.
            for seq, e in enumerate(entries):
                untimestamped.append((source_order, seq, e))
            continue

        for seq, (e, eff_ts) in enumerate(zip(entries, effective)):
            # Tie-break rank: entries that borrowed their timestamp (no
            # real stamp of their own) sort *before* a same-timestamp
            # entry that actually carries that stamp, so a banner anchored
            # to the first real line appears ahead of that line, not after.
            rank = -1 if e.timestamp is None else 0
            timestamped.append((eff_ts, rank, seq, e))

    timestamped.sort(key=lambda t: (t[0], t[1], t[2]))

    for eff_ts, _rank, _seq, e in timestamped:
        yield MergedEntry(entry=e, effective_timestamp=eff_ts, untimestamped_source=False)

    for _source_order, _seq, e in untimestamped:
        yield MergedEntry(entry=e, effective_timestamp=None, untimestamped_source=True)
