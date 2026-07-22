"""
Renders a merged stream of MergedEntry objects.

Two renderers:
  - render_stream(): the v1 human-readable output format decided during
    scoping.
  - render_json_stream(): JSON Lines output (one JSON object per entry),
    added specifically so logexplain (or anything else) can consume
    logtimeline's output as structured data via `--format json`, per the
    original design intent behind LogEntry.

Kept deliberately separate from merger.py so a structured renderer can be
added without touching the merge logic at all - both renderers only ever
consume MergedEntry objects, never produce or reorder them.
"""

from __future__ import annotations

import json
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


def entry_to_dict(merged: MergedEntry) -> dict:
    """Converts a MergedEntry to a JSON-serialisable dict.

    Field meanings:
      - timestamp: ISO8601 effective timestamp, or null if the entry's
        source never produced a real timestamp anywhere.
      - borrowed: true if this entry's own timestamp was None and it
        inherited (was anchored to) a nearby real timestamp from the same
        source - e.g. a startup banner before the first stamped line.
      - untimestamped_source: true if the entire source this entry came
        from never produced a single real timestamp.
    """
    return {
        "timestamp": merged.effective_timestamp.isoformat()
        if merged.effective_timestamp is not None
        else None,
        "source": merged.entry.source,
        "lines": merged.entry.lines,
        "borrowed": merged.entry.timestamp is None,
        "untimestamped_source": merged.untimestamped_source,
    }


def render_json_stream(entries: Iterable[MergedEntry], out: TextIO) -> None:
    """Write a merged stream to `out` as JSON Lines - one JSON object per
    entry, per line. Chosen over a single JSON array so large streams can
    be processed line-by-line downstream without loading the whole thing
    into memory."""
    for merged in entries:
        out.write(json.dumps(entry_to_dict(merged)))
        out.write("\n")
