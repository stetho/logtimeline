"""
Renders a merged stream of MergedEntry objects as a human-readable,
time-ordered text stream - the v1 output format decided during scoping.

Kept deliberately separate from merger.py so that a structured renderer
(e.g. --format json for logexplain) can be added in v2 without touching
the merge logic at all - render.py only ever consumes MergedEntry objects,
it never produces or reorders them.
"""

from __future__ import annotations

from typing import Iterable, TextIO

from .merger import MergedEntry

# Longest source label seen determines column alignment; recomputed per
# call to render_stream() rather than fixed, since source names vary
# project to project.

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _format_timestamp(merged: MergedEntry) -> str:
    if merged.untimestamped_source:
        return "?" * 10 + " " + "?" * 15
    ts = merged.effective_timestamp
    formatted = ts.strftime(_TIMESTAMP_FORMAT)[:-3]  # microseconds -> milliseconds
    marker = "~" if merged.entry.timestamp is None else " "
    return formatted + marker


def render_entry(merged: MergedEntry, source_width: int) -> str:
    """Render a single MergedEntry as one or more display lines.

    A leading "~" immediately after the timestamp marks an entry whose
    timestamp was borrowed/anchored from elsewhere in its own source
    (e.g. a banner line before the first real stamp) rather than read
    directly off that line - so it's visible in the output, not silent.
    """
    ts_str = _format_timestamp(merged)
    source_str = merged.entry.source.ljust(source_width)
    lines = merged.entry.lines

    out = [f"{ts_str} [{source_str}] {lines[0]}"]
    indent = " " * (len(ts_str) + len(source_str) + 4)
    for continuation in lines[1:]:
        out.append(f"{indent}{continuation}")
    return "\n".join(out)


def render_stream(entries: Iterable[MergedEntry], out: TextIO) -> None:
    """Write a merged stream to `out` (e.g. sys.stdout or an open file)."""
    entries = list(entries)
    if not entries:
        return
    source_width = max(len(m.entry.source) for m in entries)
    for merged in entries:
        out.write(render_entry(merged, source_width))
        out.write("\n")
