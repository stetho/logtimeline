from datetime import datetime, timezone
from io import StringIO

from logtimeline.merger import MergedEntry
from logtimeline.parser import LogEntry
from logtimeline.render import render_entry, render_stream


def dt(*args, **kwargs):
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def test_single_line_entry_renders_one_line():
    merged = MergedEntry(
        entry=LogEntry(timestamp=dt(2026, 7, 19, 0, 5, 1, 614620), source="syslog",
                        lines=["CRON[123]: (root) CMD (something)"]),
        effective_timestamp=dt(2026, 7, 19, 0, 5, 1, 614620),
        untimestamped_source=False,
    )
    line = render_entry(merged, source_width=6)
    assert line == "2026-07-19 00:05:01.614  [syslog] CRON[123]: (root) CMD (something)"


def test_continuation_lines_are_indented_under_the_first_line():
    merged = MergedEntry(
        entry=LogEntry(
            timestamp=dt(2026, 7, 17, 11, 41, 51, 271000),
            source="Jupyter",
            lines=[
                "[I 2026-07-17 11:41:51.271 ServerApp] Starting buffering...",
                "Task exception was never retrieved",
                "Traceback (most recent call last):",
            ],
        ),
        effective_timestamp=dt(2026, 7, 17, 11, 41, 51, 271000),
        untimestamped_source=False,
    )
    rendered = render_entry(merged, source_width=7)
    lines = rendered.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("2026-07-17 11:41:51.271  [Jupyter]")
    # continuation lines carry no timestamp/source prefix, just aligned indent
    assert lines[1].strip() == "Task exception was never retrieved"
    assert lines[1].startswith(" " * len("2026-07-17 11:41:51.271  [Jupyter] "))
    assert lines[2].strip() == "Traceback (most recent call last):"


def test_anchored_entry_gets_visible_borrowed_marker():
    # timestamp=None on the entry itself (borrowed from elsewhere) should
    # render with the "~" marker so it's visibly not a real read timestamp.
    merged = MergedEntry(
        entry=LogEntry(timestamp=None, source="ddns-updater", lines=["=== banner ==="]),
        effective_timestamp=dt(2026, 7, 12, 21, 10, 36),
        untimestamped_source=False,
    )
    rendered = render_entry(merged, source_width=12)
    assert "2026-07-12 21:10:36.000~" in rendered


def test_fully_untimestamped_source_renders_placeholder():
    merged = MergedEntry(
        entry=LogEntry(timestamp=None, source="mystery", lines=["line one"]),
        effective_timestamp=None,
        untimestamped_source=True,
    )
    rendered = render_entry(merged, source_width=7)
    assert rendered.startswith("?" * 10)
    assert "[mystery]" in rendered
    assert "line one" in rendered


def test_render_stream_writes_all_entries_in_order():
    entries = [
        MergedEntry(
            entry=LogEntry(timestamp=dt(2026, 1, 1, 0, 0, 1), source="a", lines=["first"]),
            effective_timestamp=dt(2026, 1, 1, 0, 0, 1),
            untimestamped_source=False,
        ),
        MergedEntry(
            entry=LogEntry(timestamp=dt(2026, 1, 1, 0, 0, 2), source="bb", lines=["second"]),
            effective_timestamp=dt(2026, 1, 1, 0, 0, 2),
            untimestamped_source=False,
        ),
    ]
    buf = StringIO()
    render_stream(entries, buf)
    output = buf.getvalue()
    assert "first" in output
    assert "second" in output
    assert output.index("first") < output.index("second")
    # source column should be aligned to the widest source name ("bb")
    assert "[a ]" in output
    assert "[bb]" in output
