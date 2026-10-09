"""Collect files, group them into seeds and run the matching plugin."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .castep.plugin import CastepPlugin
from .core import Config, Diagnostic

PLUGINS = [CastepPlugin()]

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv"}


@dataclass
class LintResult:
    diagnostics: list[Diagnostic]
    files_checked: int


def _expand(paths: Iterable[Path]) -> list[Path]:
    wanted = {s for plugin in PLUGINS for s in plugin.suffixes}
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            for sub in sorted(path.rglob("*")):
                if sub.is_file() and sub.suffix.lower() in wanted and not (_SKIP_DIRS & set(sub.parts)):
                    found.append(sub)
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(str(path))
    return found


def lint_paths(paths: Iterable[Path], config: Config | None = None) -> LintResult:
    config = config or Config()
    if config.cross_check and importlib.util.find_spec("castep_outputs") is None:
        raise RuntimeError("--cross-check needs castep-outputs: pip install 'dftlint[crosscheck]'")

    groups: dict[tuple, tuple[object, dict[str, Path]]] = {}
    for path in _expand(paths):
        suffix = path.suffix.lower()
        plugin = next((p for p in PLUGINS if suffix in p.suffixes), None)
        if plugin is None:
            continue
        key = (plugin.name, *plugin.group_key(path))
        groups.setdefault(key, (plugin, {}))[1][suffix] = path

    for plugin, files in groups.values():  # lint seed.cell together with an existing seed.param, and vice versa
        anchor = next(iter(files.values()))
        for suffix in plugin.suffixes:
            if suffix not in files:
                for candidate in (anchor.with_suffix(suffix), anchor.with_suffix(suffix.upper())):
                    if candidate.is_file():
                        files[suffix] = candidate
                        break

    diagnostics: list[Diagnostic] = []
    files_checked = 0
    for plugin, files in groups.values():
        files_checked += len(files)
        diagnostics.extend(plugin.lint_group(files, config))
    diagnostics.sort(key=lambda d: (d.file, d.line, d.col, d.rule_id))
    return LintResult(diagnostics, files_checked)
