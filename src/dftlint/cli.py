"""Command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .core import REGISTRY, Config, Severity, load_config
from .report import format_json, format_text
from .runner import lint_paths


def _split(values: list[str] | None) -> tuple[str, ...]:
    return tuple(p.strip().upper() for v in values or () for p in v.split(",") if p.strip())


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dftlint", description="Rule-based linter for DFT input files (CASTEP).")
    p.add_argument("paths", nargs="*", help=".cell/.param files or directories to search")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--select", action="append", metavar="RULES", help="only run these rule ids/prefixes (comma separated)")
    p.add_argument("--ignore", action="append", metavar="RULES", help="skip these rule ids/prefixes (comma separated)")
    p.add_argument("--config", type=Path, help="config file (default: search upwards for .dftlint.toml)")
    p.add_argument("--strict", action="store_true", help="exit non-zero on warnings as well as errors")
    p.add_argument("--cross-check", action="store_true", help="also try parsing with castep_outputs (W060)")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--list-rules", action="store_true", help="list all rules and exit")
    p.add_argument("--explain", metavar="RULE", help="describe one rule and exit")
    p.add_argument("--version", action="version", version=f"dftlint {__version__}")
    return p


def _list_rules() -> int:
    for info in sorted(REGISTRY.values(), key=lambda r: r.rule_id):
        print(f"{info.rule_id}  {str(info.severity):<7}  {info.summary}")
    return 0


def _explain(rule_id: str) -> int:
    info = REGISTRY.get(rule_id.upper())
    if info is None:
        print(f"dftlint: unknown rule '{rule_id}'", file=sys.stderr)
        return 2
    print(f"{info.rule_id} ({info.severity}): {info.summary}\n")
    print(info.explanation or "No further description.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.list_rules:
        return _list_rules()
    if args.explain:
        return _explain(args.explain)
    if not args.paths:
        print("dftlint: no paths given (try `dftlint --help`)", file=sys.stderr)
        return 2

    paths = [Path(p) for p in args.paths]
    try:
        config: Config = load_config(paths[0], args.config)
    except (OSError, ValueError) as exc:
        print(f"dftlint: bad config: {exc}", file=sys.stderr)
        return 2
    if args.select:
        config.select = _split(args.select)
    if args.ignore:
        config.ignore = (*config.ignore, *_split(args.ignore))
    if args.cross_check:
        config.cross_check = True

    try:
        result = lint_paths(paths, config)
    except FileNotFoundError as exc:
        print(f"dftlint: no such file or directory: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"dftlint: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(format_json(result.diagnostics, result.files_checked))
    else:
        color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
        print(format_text(result.diagnostics, result.files_checked, color))

    threshold = Severity.WARNING if args.strict else Severity.ERROR
    return 1 if any(d.severity >= threshold for d in result.diagnostics) else 0


if __name__ == "__main__":
    sys.exit(main())
