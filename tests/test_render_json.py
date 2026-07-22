import json
from datetime import datetime, timezone
from io import StringIO

from logtimeline.merger import MergedEntry
from logtimeline.parser import LogEntry
from logtimeline.render import render_json_stream, entry_to_dict


def dt(*args, **kwargs):
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def test_entry_to_dict_normal_entry():
    merged = MergedEntry(
        entry=LogEntry(timestamp=dt(2026, 7, 19, 0, 5, 1), source="syslog", lines=["CRON started"]),
        effective_timestamp=dt(2026, 7, 19, 0, 5, 1),
        untimestamped_source=False,
    )
    d = entry_to_dict(merged)
    assert d["timestamp"] == "2026-07-19T00:05:01+00:00"
    assert d["source"] == "syslog"
    assert d["lines"] == ["CRON started"]
    assert d["borrowed"] is False
    assert d["untimestamped_source"] is False


def test_entry_to_dict_borrowed_timestamp():
    merged = MergedEntry(
        entry=LogEntry(timestamp=None, source="ddns", lines=["banner"]),
        effective_timestamp=dt(2026, 7, 12, 21, 10, 36),
        untimestamped_source=False,
    )
    d = entry_to_dict(merged)
    assert d["timestamp"] == "2026-07-12T21:10:36+00:00"
    assert d["borrowed"] is True


def test_entry_to_dict_untimestamped_source():
    merged = MergedEntry(
        entry=LogEntry(timestamp=None, source="mystery", lines=["line"]),
        effective_timestamp=None,
        untimestamped_source=True,
    )
    d = entry_to_dict(merged)
    assert d["timestamp"] is None
    assert d["untimestamped_source"] is True


def test_render_json_stream_is_valid_jsonl_with_multiline_entries():
    entries = [
        MergedEntry(
            entry=LogEntry(
                timestamp=dt(2026, 1, 1, 0, 0, 0), source="a",
                lines=["Traceback (most recent call last):", "  File x", "ValueError: boom"],
            ),
            effective_timestamp=dt(2026, 1, 1, 0, 0, 0),
            untimestamped_source=False,
        ),
        MergedEntry(
            entry=LogEntry(timestamp=dt(2026, 1, 1, 0, 0, 1), source="b", lines=["ok"]),
            effective_timestamp=dt(2026, 1, 1, 0, 0, 1),
            untimestamped_source=False,
        ),
    ]
    buf = StringIO()
    render_json_stream(entries, buf)
    lines = buf.getvalue().strip().split("\n")
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["lines"] == [
        "Traceback (most recent call last):", "  File x", "ValueError: boom"
    ]
    second = json.loads(lines[1])
    assert second["source"] == "b"
