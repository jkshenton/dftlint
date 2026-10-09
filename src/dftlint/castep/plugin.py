"""CASTEP plugin: parses a seed's .cell/.param pair and runs the registered rules."""

from __future__ import annotations

from pathlib import Path

from ..core import REGISTRY, Config, Diagnostic, Severity
from . import rules  # noqa: F401  (importing registers the rules)
from .context import Context, FileView
from .parse import parse_text
from .schema import load_schema


class CastepPlugin:
    name = "castep"
    suffixes = (".cell", ".param")

    def group_key(self, path: Path) -> tuple:
        return (path.resolve().parent, path.stem)

    def lint_group(self, files: dict[str, Path], config: Config) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        views: dict[str, FileView] = {}
        for suffix, kind in ((".cell", "cell"), (".param", "param")):
            path = files.get(suffix)
            if path is None:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                if config.enabled("E000"):
                    diagnostics.append(
                        Diagnostic("E000", Severity.ERROR, f"Cannot read file: {exc.strerror or exc}", str(path), 1, 1)
                    )
                continue
            pf = parse_text(text, str(path), kind)
            views[kind] = FileView(pf, load_schema(kind, config.extra_keywords))

        ctx = Context(views.get("cell"), views.get("param"), config)
        anchor = (ctx.views() or [None])[0]
        for info in sorted(REGISTRY.values(), key=lambda r: r.rule_id):
            if info.scope != self.name or info.func is None or not config.enabled(info.rule_id):
                continue
            try:
                info.func(ctx)
            except Exception as exc:  # a buggy rule must not hide every other finding
                if anchor is not None and config.enabled("E999"):
                    diagnostics.append(
                        Diagnostic(
                            "E999", Severity.ERROR,
                            f"Internal error in rule {info.rule_id}: {type(exc).__name__}: {exc}",
                            anchor.pf.path, 1, 1,
                        )
                    )
        if config.cross_check:
            self._cross_check(ctx)
        return diagnostics + ctx.diagnostics

    @staticmethod
    def _cross_check(ctx: Context) -> None:
        import castep_outputs  # availability is checked up front by the runner

        for view in ctx.views():
            parse = castep_outputs.parse_cell_file if view.kind == "cell" else castep_outputs.parse_param_file
            try:
                with open(view.pf.path, encoding="utf-8", errors="replace") as fh:
                    parse(fh)
            except Exception as exc:
                ctx.report(
                    "W060", view, 1, 1, None,
                    f"castep_outputs could not parse this file ({type(exc).__name__}: {exc}).",
                )
