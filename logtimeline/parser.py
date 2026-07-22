"""
Parses raw log lines from any source into a stream of LogEntry objects.

Design principle (learned from lnav's open false-positive issue on naive
error-keyword matching): timestamp detection here is deliberately anchored
and pattern-based, never a free-text search for "anything date-shaped
anywhere in the line". A pattern only matches at the *start* of a line
(after an optional docker-compose-style "container_name | " prefix is
stripped for detection purposes only). This avoids misreading things like
IDs, ports, or numbers embedded mid-line as timestamps, and it means a line
with no recognisable timestamp is treated as a continuation of the previous
entry rather than silently dropped or misclassified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator, Optional

# Optional dependency: only needed if a caller supplies a custom strptime
# override format string containing directives datetime.strptime can't
# handle on its own edge cases. Kept out of the hot path otherwise.


@dataclass
class LogEntry:
    """A single logical log entry: one timestamped line plus any
    untimestamped continuation lines that followed it (stack traces,
    multi-line banners, pretty-printed JSON, etc.)."""

    timestamp: Optional[datetime]
    source: str
    lines: list[str] = field(default_factory=list)

    @property
    def raw_line(self) -> str:
        return self.lines[0] if self.lines else ""

    @property
    def is_continuation_only(self) -> bool:
        """True if this entry never got a real timestamp (e.g. banner
        lines before the first stamped line in a source)."""
        return self.timestamp is None


# --- Timestamp patterns -----------------------------------------------
#
# Tried in order, first match wins. Each entry is:
#   (name, compiled regex with the timestamp as group(1), parser function)
#
# Order matters: more specific / longer patterns first, so e.g. the
# fractional-seconds RFC3339 pattern is tried before the plain one.

def _parse_rfc3339(fmt: str) -> Callable[[str], datetime]:
    def parse(s: str) -> datetime:
        return datetime.strptime(s, fmt)
    return parse


def _parse_offset(s: str) -> datetime:
    # Handles "2026-07-19T00:00:00.212478+00:00" style (Python 3.11+ handles
    # this natively via fromisoformat; we support 3.10 too by normalising).
    s2 = s
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    return datetime.fromisoformat(s2)


_PATTERNS: list[tuple[str, re.Pattern, Callable[[str], datetime]]] = [
    (
        "rfc3339_offset",
        re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"),
        _parse_offset,
    ),
    (
        "bracket_level_space_date",
        # e.g. "[I 2026-07-17 09:00:31.209 ServerApp]" (Jupyter/tornado style)
        re.compile(r"^\[\w\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s"),
        _parse_rfc3339("%Y-%m-%d %H:%M:%S.%f"),
    ),
    (
        "space_date_time_frac",
        # e.g. "2026-07-12 21:10:37.146 UTC ..." (postgres) or
        # "2026-07-13 03:19:08.463  WARN ---" (airsonic)
        re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)"),
        _parse_rfc3339("%Y-%m-%d %H:%M:%S.%f"),
    ),
    (
        "space_date_time",
        # e.g. "2026-07-14 16:11:42 - ERROR ::" (tautulli)
        re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"),
        _parse_rfc3339("%Y-%m-%d %H:%M:%S"),
    ),
]

# Strips an optional "container_name   | " prefix (docker compose combined
# log style) purely for the purposes of *detecting* a timestamp. The raw
# line stored on the LogEntry is never modified.
_COMPOSE_PREFIX = re.compile(r"^(\S+)\s+\|\s?(.*)$")


def _line_for_detection(line: str) -> str:
    m = _COMPOSE_PREFIX.match(line)
    return m.group(2) if m else line


def detect_timestamp(
    line: str, override_format: Optional[str] = None
) -> Optional[datetime]:
    """Attempt to find a timestamp at the start of a line. Returns a
    timezone-aware datetime (naive timestamps are assumed UTC), or None
    if no timestamp was found."""

    candidate = _line_for_detection(line)

    if override_format is not None:
        # strptime requires an exact full-string match, but we only know
        # the *format* of the timestamp, not its exact character length
        # (e.g. "%d" may be 1 or 2 digits) - so grow the candidate prefix
        # until it either parses or we give up. This is a few extra cheap
        # attempts per line, not a performance concern for log parsing.
        for end in range(6, min(len(candidate), 40) + 1):
            try:
                dt = datetime.strptime(candidate[:end], override_format)
            except ValueError:
                continue
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return None

    for _name, pattern, parse_fn in _PATTERNS:
        m = pattern.match(candidate)
        if not m:
            continue
        try:
            dt = parse_fn(m.group(1))
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    return None


def parse_lines(
    lines: Iterable[str], source: str, override_format: Optional[str] = None
) -> Iterator[LogEntry]:
    """Groups raw lines from a single source into LogEntry objects.

    A line with a detected timestamp starts a new entry. A line without one
    is appended as a continuation of the current entry (or starts a new,
    timestamp-less entry if there is no current one yet - e.g. a banner
    before the first stamped line).
    """
    current: Optional[LogEntry] = None

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        ts = detect_timestamp(line, override_format)

        if ts is not None:
            if current is not None:
                yield current
            current = LogEntry(timestamp=ts, source=source, lines=[line])
        else:
            if current is None:
                current = LogEntry(timestamp=None, source=source, lines=[line])
            else:
                current.lines.append(line)

    if current is not None:
        yield current


def parse_file(
    path: str, source: str, override_format: Optional[str] = None
) -> Iterator[LogEntry]:
    with open(path, "r", errors="replace") as f:
        yield from parse_lines(f, source, override_format)
