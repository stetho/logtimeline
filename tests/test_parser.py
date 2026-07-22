import os
from datetime import timezone

from logtimeline.parser import detect_timestamp, parse_file

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


# --- detect_timestamp: unit-level pattern checks -----------------------

def test_detects_journald_offset_timestamp():
    line = "2026-07-19T00:00:00.212478+00:00 proliant1 systemd[1]: logrotate.service: Deactivated successfully."
    ts = detect_timestamp(line)
    assert ts is not None
    assert ts.year == 2026 and ts.month == 7 and ts.day == 19
    assert ts.tzinfo is not None


def test_detects_docker_timestamps_rfc3339nano_z():
    line = "2026-07-12T22:05:01.973369008Z  * Serving Flask app 'app'"
    ts = detect_timestamp(line)
    assert ts is not None
    assert ts.hour == 22 and ts.minute == 5


def test_detects_postgres_style_timestamp_with_compose_prefix():
    line = 'kbdb         | 2026-07-12 21:10:37.085 UTC [1] LOG:  starting PostgreSQL 18.4'
    ts = detect_timestamp(line)
    assert ts is not None
    assert ts.year == 2026 and ts.hour == 21 and ts.minute == 10

    # microseconds should have been parsed too (six digits: .085 -> 085000 us
    # is wrong; "085" is milliseconds so strptime %f reads it as 85000 us)
    assert ts.microsecond == 85000


def test_no_timestamp_returns_none_for_banner_line():
    line = "ddns-updater  | ========================================"
    assert detect_timestamp(line) is None


def test_manual_override_format():
    # A deliberately awkward format the auto-detector wouldn't otherwise
    # recognise, to confirm the override path works independently.
    line = "19/Jul/2026:00:00:00 some nginx-combined-style line"
    ts = detect_timestamp(line, override_format="%d/%b/%Y:%H:%M:%S")
    assert ts is not None
    assert ts.year == 2026 and ts.month == 7 and ts.day == 19


# --- parse_file: integration checks against real proliant1 fixtures ----

def test_syslog_fixture_parses_every_line_as_its_own_entry():
    entries = list(parse_file(fixture("syslog"), source="syslog"))
    assert len(entries) > 100
    # journald export - every line should carry a real timestamp, no
    # continuation lines expected in this fixture
    assert all(e.timestamp is not None for e in entries)
    assert all(len(e.lines) == 1 for e in entries)
    assert entries[0].timestamp.tzinfo is not None


def test_docker_timestamps_fixture_single_container():
    entries = list(parse_file(fixture("sample-docker1.log"), source="flask-app"))
    assert len(entries) == 2
    assert entries[0].raw_line.endswith("* Serving Flask app 'app'")
    assert entries[1].raw_line.endswith("* Debug mode: on")
    # docker --timestamps stamps every single line, including this banner
    assert all(e.timestamp is not None for e in entries)


def test_docker_timestamps_fixture_ascii_banner_still_gets_stamped():
    entries = list(parse_file(fixture("sample-docker2.log"), source="swag"))
    # With --timestamps, even ASCII-art banner lines get their own Docker
    # timestamp, so this fixture should NOT exercise continuation grouping -
    # every line is its own entry.
    assert all(e.timestamp is not None for e in entries)
    assert all(len(e.lines) == 1 for e in entries)


def test_compose_dump_multiline_traceback_grouped_as_continuation():
    entries = list(
        parse_file(
            fixture("sample-docker-compose-dump.log"),
            source="sample_docker_compose_dump",
        )
    )

    # Find the entry whose first line is the "Starting buffering..." log
    # that precedes the real Jupyter/tornado traceback in the fixture.
    trigger = next(
        e for e in entries
        if "Starting buffering for" in e.raw_line and "11:41:51" in e.raw_line
    )

    # The traceback and its surrounding untimestamped lines (12 lines: from
    # "Task exception was never retrieved" through
    # "tornado.websocket.WebSocketClosedError") should all have been
    # attached to this entry as continuation lines, not split into their
    # own entries or dropped.
    assert "Task exception was never retrieved" in trigger.lines[1]
    assert any("Traceback (most recent call last):" in l for l in trigger.lines)
    assert any("tornado.websocket.WebSocketClosedError" in l for l in trigger.lines)
    assert len(trigger.lines) >= 13  # trigger line + 12 continuation lines

    # And the next real timestamped line (the following [I ...] ServerApp
    # entry) should have started a fresh entry, not been swallowed too.
    next_entry = entries[entries.index(trigger) + 1]
    assert next_entry.timestamp is not None
    assert "Adapting from protocol version" in next_entry.raw_line


def test_compose_dump_leading_banner_with_no_prior_timestamp():
    entries = list(
        parse_file(
            fixture("sample-docker-compose-dump.log"),
            source="sample_docker_compose_dump",
        )
    )
    # The very first line in the fixture ("postgres  | ") has no timestamp
    # and comes before any stamped line - it should still surface as an
    # entry (timestamp=None) rather than being silently dropped.
    assert entries[0].timestamp is None
    assert entries[0].is_continuation_only
