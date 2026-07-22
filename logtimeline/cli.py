"""
Command-line entry point for logtimeline.

Usage examples:
    python3 -m logtimeline.cli --file /var/log/syslog --docker swag --docker jupyter
    python3 -m logtimeline.cli --file /var/log/syslog=host --file app.log=myapp -o out.log
    python3 -m logtimeline.cli --docker weird-app --override weird-app=%d/%b/%Y:%H:%M:%S

Source naming: both --file and --docker accept an optional "=NAME" suffix to
set the source label used in output. Without it, --file uses the filename
and --docker uses the container name.

--override lets a specific source's timestamp format be pinned explicitly
(strptime format string) rather than relying on auto-detection - useful for
a source whose format the auto-detector doesn't recognise.

A --docker source that fails to read (container not found, docker not
installed, etc.) is reported as a warning and skipped rather than aborting
the whole run - useful when merging several sources and one is temporarily
unavailable. The run only fails outright if *no* sources could be read.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import TextIO

from .parser import parse_file, parse_lines
from .merger import merge_sources
from .render import render_stream, render_json_stream


def parse_source_spec(spec: str, default_name_fn) -> tuple[str, str]:
    """Splits "value=name" into (value, name), or (value, default_name_fn(value))
    if no "=name" suffix is present."""
    if "=" in spec:
        value, name = spec.split("=", 1)
        return value, name
    return spec, default_name_fn(spec)


def parse_override_spec(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise ValueError(
            f"--override must be in the form SOURCE=FORMAT, got: {spec!r}"
        )
    source, fmt = spec.split("=", 1)
    return source, fmt


def run_docker_logs(container: str) -> list[str]:
    """Runs `docker logs --timestamps <container>` and returns its output
    as a list of lines. Raises RuntimeError with a clear message on any
    failure (docker missing, container not found, permission issues,
    etc.) so callers can decide whether to skip or abort."""
    try:
        result = subprocess.run(
            ["docker", "logs", "--timestamps", container],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "docker command not found - is Docker installed and on PATH?"
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "(no error output)"
        raise RuntimeError(f"docker logs failed for '{container}': {stderr}")

    # docker logs writes to both stdout and stderr depending on the
    # container's own stream usage - combine both so nothing is lost.
    combined = (result.stdout or "") + (result.stderr or "")
    return combined.splitlines()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="logtimeline",
        description="Merge log files and docker container logs into one time-ordered stream.",
    )
    p.add_argument(
        "--file",
        action="append",
        default=[],
        dest="files",
        metavar="PATH[=NAME]",
        help="A log file to include. Optional =NAME sets the source label "
        "(defaults to the filename). Repeatable.",
    )
    p.add_argument(
        "--docker",
        action="append",
        default=[],
        dest="docker_containers",
        metavar="CONTAINER[=NAME]",
        help="A running container to read via `docker logs --timestamps`. "
        "Optional =NAME sets the source label (defaults to the container "
        "name). Repeatable.",
    )
    p.add_argument(
        "--override",
        action="append",
        default=[],
        dest="overrides",
        metavar="SOURCE=FORMAT",
        help="Pin an explicit strptime format for a specific source's "
        "timestamps instead of relying on auto-detection. Repeatable.",
    )
    p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format. 'text' is the human-readable v1 default. "
        "'json' emits JSON Lines (one object per entry) for downstream "
        "tools such as logexplain.",
    )
    p.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Write the merged output to this file instead of stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.files and not args.docker_containers:
        print("logtimeline: no sources given (use --file and/or --docker)", file=sys.stderr)
        return 1

    overrides: dict[str, str] = {}
    for spec in args.overrides:
        try:
            source, fmt = parse_override_spec(spec)
        except ValueError as e:
            print(f"logtimeline: {e}", file=sys.stderr)
            return 1
        overrides[source] = fmt

    sources = {}

    for spec in args.files:
        path, name = parse_source_spec(spec, default_name_fn=lambda p: p.rsplit("/", 1)[-1])
        try:
            sources[name] = list(parse_file(path, source=name, override_format=overrides.get(name)))
        except OSError as e:
            print(f"logtimeline: warning: could not read file '{path}': {e}", file=sys.stderr)

    for spec in args.docker_containers:
        container, name = parse_source_spec(spec, default_name_fn=lambda c: c)
        try:
            lines = run_docker_logs(container)
        except RuntimeError as e:
            print(f"logtimeline: warning: {e}", file=sys.stderr)
            continue
        sources[name] = list(parse_lines(lines, source=name, override_format=overrides.get(name)))

    if not sources:
        print("logtimeline: no sources could be read - nothing to do", file=sys.stderr)
        return 1

    merged = merge_sources(sources)
    renderer = render_json_stream if args.format == "json" else render_stream

    out: TextIO
    if args.output:
        with open(args.output, "w") as out:
            renderer(merged, out)
    else:
        renderer(merged, sys.stdout)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
