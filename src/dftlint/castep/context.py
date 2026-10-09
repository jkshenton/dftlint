"""Per-seed analysis context shared by all CASTEP rules."""

from __future__ import annotations

from functools import cached_property

from ..core import REGISTRY, Config, Diagnostic
from .blocks import (
    Atom,
    BlockIssue,
    PotEntry,
    parse_lattice,
    parse_positions,
    parse_species_pot,
)
from .geometry import Lattice
from .parse import Block, Entry, ParsedFile, Row
from .schema import Schema, parse_bool

Node = Entry | Block


class FileView:
    """A parsed file plus its schema, indexed by canonical keyword name."""

    def __init__(self, pf: ParsedFile, schema: Schema):
        self.pf = pf
        self.schema = schema
        self._index: dict[str, list[Node]] = {}
        for node in pf.nodes():
            canon = schema.canonical(node.key) or node.key
            self._index.setdefault(canon, []).append(node)

    @property
    def kind(self) -> str:
        return self.pf.kind

    def nodes_named(self, name: str) -> list[Node]:
        return list(self._index.get(name, ()))

    def first(self, name: str) -> Node | None:
        nodes = self._index.get(name)
        return nodes[0] if nodes else None

    def has(self, *names: str) -> bool:
        return any(n in self._index for n in names)

    def value(self, name: str) -> str | None:
        """Value of the first scalar entry called ``name`` (None if absent)."""
        for node in self._index.get(name, ()):
            if isinstance(node, Entry):
                return node.value
        return None

    def flag(self, name: str) -> bool | None:
        """Boolean value of a scalar; a bare keyword in a .cell file means true."""
        for node in self._index.get(name, ()):
            if isinstance(node, Entry):
                if node.value == "":
                    return True if self.kind == "cell" else None
                return parse_bool(node.value)
        return None

    @cached_property
    def lattice(self) -> tuple[Lattice | None, list[BlockIssue]]:
        for name in ("lattice_cart", "lattice_abc"):
            node = self.first(name)
            if isinstance(node, Block):
                return parse_lattice(node)
        return None, []

    @cached_property
    def positions(self) -> tuple[list[Atom], list[BlockIssue]]:
        atoms: list[Atom] = []
        issues: list[BlockIssue] = []
        for name, frac in (("positions_frac", True), ("positions_abs", False)):
            for node in self.nodes_named(name):
                if isinstance(node, Block):
                    found, problems = parse_positions(node, frac)
                    atoms += found
                    issues += problems
        return atoms, issues

    @cached_property
    def species_pot(self) -> tuple[dict[str, PotEntry], list[BlockIssue]]:
        node = self.first("species_pot")
        if isinstance(node, Block):
            return parse_species_pot(node)
        return {}, []


class Context:
    def __init__(self, cell: FileView | None, param: FileView | None, config: Config):
        self.cell = cell
        self.param = param
        self.config = config
        self.diagnostics: list[Diagnostic] = []

    def views(self) -> list[FileView]:
        return [v for v in (self.cell, self.param) if v is not None]

    def report(
        self, rule_id: str, view: FileView, line: int, col: int, end_col: int | None, message: str
    ) -> None:
        if not self.config.enabled(rule_id) or view.pf.is_suppressed(rule_id, line):
            return
        self.diagnostics.append(
            Diagnostic(rule_id, REGISTRY[rule_id].severity, message, view.pf.path, line, col, end_col)
        )

    def report_node(self, rule_id: str, view: FileView, node: Node, message: str) -> None:
        self.report(rule_id, view, node.line, node.col, node.end_col, message)

    def report_row(self, rule_id: str, view: FileView, row: Row, message: str) -> None:
        self.report(rule_id, view, row.line, row.col, row.end_col, message)

    def report_issue(self, rule_id: str, view: FileView, issue: BlockIssue) -> None:
        self.report(rule_id, view, issue.line, issue.col, issue.end_col, issue.message)
