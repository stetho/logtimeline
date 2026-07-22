import os
from datetime import datetime, timezone

from logtimeline.merger import merge_sources
from logtimeline.parser import LogEntry, parse_file

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


def dt(*args, **kwargs):
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


# --- Core interleaving, using real fixtures -----------------------------

def test_merges_two_real_sources_in_time_order():
    sources = {
        "syslog": parse_file(fixture("syslog"), source="syslog"),
        "flask-app": parse_file(fixture("sample-docker1.log"), source="flask-app"),
    }
    merged = list(merge_sources(sources))

    timestamps = [m.effective_timestamp for m in merged]
    assert timestamps == sorted(timestamps)

    # Both sources' entries should actually be present, interleaved by time,
    # not just concatenated.
    source_labels = [m.entry.source for m in merged]
    assert "syslog" in source_labels
    assert "flask-app" in source_labels
    # The two flask-app lines are from 2026-07-12 22:05, which should place
    # them somewhere in the middle of syslog's 2026-07-19 span... actually
    # they're a different day entirely (12th vs 19th), so flask-app's two
    # entries should be the very first two entries in the merged stream.
    assert merged[0].entry.source == "flask-app"
    assert merged[1].entry.source == "flask-app"
    assert merged[2].entry.source == "syslog"


# --- Anchoring behaviour for untimestamped leading entries ---------------

def test_leading_banner_anchored_before_first_real_timestamp_same_source():
    entries = [
        LogEntry(timestamp=None, source="ddns-updater", lines=["===banner==="]),
        LogEntry(timestamp=None, source="ddns-updater", lines=["=== more banner ==="]),
        LogEntry(timestamp=dt(2026, 7, 12, 21, 10, 36), source="ddns-updater",
                  lines=["2026-07-12T21:10:36Z INFO Settings summary:"]),
        LogEntry(timestamp=dt(2026, 7, 12, 21, 10, 40), source="ddns-updater",
                  lines=["2026-07-12T21:10:40Z INFO next line"]),
    ]
    merged = list(merge_sources({"ddns-updater": entries}))

    # All four entries should appear, in original order, with the two
    # banner lines anchored to (and sorted just before) the first real
    # timestamp - not dropped, not shoved arbitrarily to the global start.
    assert [m.entry.lines[0] for m in merged] == [
        "===banner===",
        "=== more banner ===",
        "2026-07-12T21:10:36Z INFO Settings summary:",
        "2026-07-12T21:10:40Z INFO next line",
    ]
    assert merged[0].effective_timestamp == dt(2026, 7, 12, 21, 10, 36)
    assert merged[1].effective_timestamp == dt(2026, 7, 12, 21, 10, 36)
    assert merged[0].untimestamped_source is False  # anchored, not "unknown"


def test_banner_anchored_correctly_when_interleaved_with_other_sources():
    # A banner-then-stamped source, interleaved with a second source whose
    # real timestamps fall *between* the banner and the first real
    # timestamp of the first source in wall-clock time - if anchoring
    # works, the banner still sticks with its own source's first real
    # entry rather than drifting to wherever it happens to sort.
    ddns = [
        LogEntry(timestamp=None, source="ddns-updater", lines=["banner"]),
        LogEntry(timestamp=dt(2026, 7, 12, 21, 10, 36), source="ddns-updater", lines=["real-A"]),
    ]
    other = [
        LogEntry(timestamp=dt(2026, 7, 12, 21, 10, 30), source="other", lines=["other-earlier"]),
    ]
    merged = list(merge_sources({"ddns-updater": ddns, "other": other}))

    order = [m.entry.lines[0] for m in merged]
    # other-earlier (21:10:30) comes before banner+real-A (anchored/actual
    # 21:10:36), and the banner still immediately precedes real-A.
    assert order == ["other-earlier", "banner", "real-A"]


# --- Fully untimestamped source -----------------------------------------

def test_source_with_no_real_timestamp_anywhere_goes_to_end_flagged():
    mystery = [
        LogEntry(timestamp=None, source="mystery", lines=["line one"]),
        LogEntry(timestamp=None, source="mystery", lines=["line two"]),
    ]
    dated = [
        LogEntry(timestamp=dt(2026, 1, 1, 0, 0, 0), source="dated", lines=["dated line"]),
    ]
    merged = list(merge_sources({"mystery": mystery, "dated": dated}))

    assert merged[0].entry.source == "dated"
    assert merged[0].untimestamped_source is False

    assert merged[1].entry.lines == ["line one"]
    assert merged[1].untimestamped_source is True
    assert merged[2].entry.lines == ["line two"]
    assert merged[2].untimestamped_source is True


# --- Multi-line continuation entries survive the merge intact -----------

def test_continuation_lines_stay_attached_through_merge():
    sources = {
        "compose-dump": parse_file(
            fixture("sample-docker-compose-dump.log"), source="compose-dump"
        ),
        "syslog": parse_file(fixture("syslog"), source="syslog"),
    }
    merged = list(merge_sources(sources))

    traceback_entry = next(
        m for m in merged
        if "Starting buffering for" in m.entry.raw_line and "11:41:51" in m.entry.raw_line
    )
    assert any(
        "tornado.websocket.WebSocketClosedError" in l for l in traceback_entry.entry.lines
    )
    assert len(traceback_entry.entry.lines) >= 13
