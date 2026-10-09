"""Core types shared by every code plugin: diagnostics, rule registry, config."""

from __future__ import annotations

import inspect
import tomllib
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2

    @classmethod
    def parse(cls, text: str) -> "Severity":
        try:
            return cls[str(text).strip().upper()]
        except KeyError:
            raise ValueError(
                f"unknown severity {text!r} (use error, warning or info)"
            ) from None

    def __str__(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Diagnostic:
    """One finding. ``line``/``col`` are 1-based; ``end_col`` is exclusive."""

    rule_id: str
    severity: Severity
    message: str
    file: str
    line: int
    col: int = 1
    end_col: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule_id,
            "severity": str(self.severity),
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "end_col": self.end_col,
        }


@dataclass(frozen=True)
class RuleInfo:
    rule_id: str
    severity: Severity
    summary: str
    explanation: str
    scope: str
    func: Callable[..., None] | None


REGISTRY: dict[str, RuleInfo] = {}


def rule(rule_id: str, severity: str, summary: str, scope: str = "castep"):
    """Register a rule function. The function's docstring becomes ``--explain`` text."""

    def decorator(func: Callable[..., None]) -> Callable[..., None]:
        if rule_id in REGISTRY:
            raise ValueError(f"duplicate rule id {rule_id}")
        REGISTRY[rule_id] = RuleInfo(
            rule_id,
            Severity.parse(severity),
            summary,
            inspect.cleandoc(func.__doc__ or ""),
            scope,
            func,
        )
        return func

    return decorator


def register_meta_rule(
    rule_id: str, severity: str, summary: str, explanation: str, scope: str = "castep"
) -> None:
    """Register a rule id that is reported by the framework or by another rule."""
    REGISTRY[rule_id] = RuleInfo(
        rule_id, Severity.parse(severity), summary, explanation, scope, None
    )


@dataclass
class Config:
    select: tuple[str, ...] | None = None
    ignore: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    extra_keywords: tuple[str, ...] = ()
    cross_check: bool = False

    def enabled(self, rule_id: str) -> bool:
        """Prefix matching, as in ruff: ``W`` covers every warning rule."""
        if any(rule_id.startswith(p) for p in self.ignore):
            return False
        return self.select is None or any(rule_id.startswith(p) for p in self.select)

    def option(self, name: str, default: Any) -> Any:
        return self.options.get(name, default)


_CONFIG_KEYS = {
    "select",
    "ignore",
    "options",
    "extra_keywords",
    "cross_check",
}


def config_from_dict(data: dict[str, Any]) -> Config:
    unknown = set(data) - _CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"unknown dftlint config key(s): {', '.join(sorted(unknown))}"
        )
    select = data.get("select")
    return Config(
        select=tuple(s.upper() for s in select) if select is not None else None,
        ignore=tuple(s.upper() for s in data.get("ignore", ())),
        options=dict(data.get("options", {})),
        extra_keywords=tuple(k.lower() for k in data.get("extra_keywords", ())),
        cross_check=bool(data.get("cross_check", False)),
    )


def find_config_file(start: Path) -> Path | None:
    """Look upwards for ``.dftlint.toml``."""
    directory = start if start.is_dir() else start.parent
    for d in (directory.resolve(), *directory.resolve().parents):
        if (d / ".dftlint.toml").is_file():
            return d / ".dftlint.toml"
    return None


def load_config(start: Path | None = None, explicit: Path | None = None) -> Config:
    path = explicit or (find_config_file(start) if start is not None else None)
    if path is None:
        return Config()
    with path.open("rb") as fh:
        return config_from_dict(tomllib.load(fh))
