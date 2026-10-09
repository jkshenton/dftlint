"""Output formats: text and json."""

from __future__ import annotations

import json

from .core import Diagnostic, Severity

_COLORS = {Severity.ERROR: "31", Severity.WARNING: "33", Severity.INFO: "36"}


def _paint(text: str, code: str, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def format_text(diagnostics: list[Diagnostic], files_checked: int, color: bool = False) -> str:
    lines = []
    for d in diagnostics:
        severity = _paint(f"{d.rule_id} {d.severity}", _COLORS[d.severity], color)
        lines.append(f"{d.file}:{d.line}:{d.col}: {severity} {d.message}")
    counts = {s: sum(1 for d in diagnostics if d.severity is s) for s in Severity}
    if not diagnostics:
        lines.append(f"All checks passed ({files_checked} file{'s' if files_checked != 1 else ''}).")
    else:
        lines.append(
            f"Found {counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), "
            f"{counts[Severity.INFO]} info in {files_checked} file(s)."
        )
    return "\n".join(lines)


def format_json(diagnostics: list[Diagnostic], files_checked: int) -> str:
    return json.dumps(
        {"files_checked": files_checked, "diagnostics": [d.to_dict() for d in diagnostics]}, indent=2
    )
