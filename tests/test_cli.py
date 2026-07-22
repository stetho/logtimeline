import os
import subprocess

import pytest

from logtimeline.cli import (
    parse_source_spec,
    parse_override_spec,
    run_docker_logs,
    main,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


# --- spec parsing (pure functions, no subprocess/filesystem needed) -----

def test_parse_source_spec_with_explicit_name():
    value, name = parse_source_spec("swag=my-swag", default_name_fn=lambda c: c)
    assert value == "swag"
    assert name == "my-swag"


def test_parse_source_spec_without_name_uses_default():
    value, name = parse_source_spec("swag", default_name_fn=lambda c: c.upper())
    assert value == "swag"
    assert name == "SWAG"


def test_parse_source_spec_file_default_uses_basename():
    default_fn = lambda p: p.rsplit("/", 1)[-1]
    value, name = parse_source_spec("/var/log/syslog", default_name_fn=default_fn)
    assert value == "/var/log/syslog"
    assert name == "syslog"


def test_parse_override_spec_valid():
    source, fmt = parse_override_spec("weird-app=%d/%b/%Y:%H:%M:%S")
    assert source == "weird-app"
    assert fmt == "%d/%b/%Y:%H:%M:%S"


def test_parse_override_spec_missing_equals_raises():
    with pytest.raises(ValueError):
        parse_override_spec("no-equals-sign-here")


# --- run_docker_logs, with subprocess mocked ------------------------------

def test_run_docker_logs_returns_lines_on_success(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        assert cmd == ["docker", "logs", "--timestamps", "myapp"]
        return subprocess.CompletedProcess(cmd, 0, stdout="line1\nline2\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    lines = run_docker_logs("myapp")
    assert lines == ["line1", "line2"]


def test_run_docker_logs_raises_clear_error_when_docker_missing(monkeypatch):
    def fake_run(*a, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="docker command not found"):
        run_docker_logs("myapp")


def test_run_docker_logs_raises_clear_error_on_container_failure(monkeypatch):
    def fake_run(cmd, capture_output, text, check):
        raise subprocess.CalledProcessError(
            1, cmd, stderr="Error: No such container: myapp"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="No such container"):
        run_docker_logs("myapp")


# --- full integration: main() against real fixtures, files only ----------

def test_main_merges_real_files_and_writes_output(tmp_path):
    out_file = tmp_path / "merged.log"
    exit_code = main([
        "--file", f"{fixture('syslog')}=syslog",
        "--file", f"{fixture('sample-docker1.log')}=flask-app",
        "-o", str(out_file),
    ])
    assert exit_code == 0

    content = out_file.read_text()
    assert "[syslog" in content
    assert "[flask-app" in content
    assert "Serving Flask app" in content

    # flask-app entries (2026-07-12) should appear before syslog entries
    # (2026-07-19) in the merged, time-ordered output.
    assert content.index("Serving Flask app") < content.index("CRON")


def test_main_returns_error_code_when_no_sources_given():
    assert main([]) == 1


def test_main_skips_unreadable_file_but_continues_with_others(tmp_path, capsys):
    out_file = tmp_path / "merged.log"
    exit_code = main([
        "--file", "/this/path/does/not/exist.log",
        "--file", f"{fixture('sample-docker1.log')}=flask-app",
        "-o", str(out_file),
    ])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err
    content = out_file.read_text()
    assert "Serving Flask app" in content


def test_main_with_override_format(tmp_path):
    # Build a tiny log with an awkward timestamp format the auto-detector
    # won't recognise, and confirm --override makes it parse correctly.
    weird_log = tmp_path / "weird.log"
    weird_log.write_text("19/Jul/2026:12:00:00 something happened\n")
    out_file = tmp_path / "merged.log"

    exit_code = main([
        "--file", f"{weird_log}=weird",
        "--override", "weird=%d/%b/%Y:%H:%M:%S",
        "-o", str(out_file),
    ])
    assert exit_code == 0
    content = out_file.read_text()
    assert "2026-07-19 12:00:00" in content
    assert "something happened" in content
