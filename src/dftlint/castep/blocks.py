"""Interpretation of block contents: lattice, positions, species_pot, k-point lists.

Each parser returns ``(result, issues)``. Issues carry positions so rule E016 can
point at the offending row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .geometry import Lattice, lattice_from_abc
from .numbers import is_real, to_float
from .parse import Block, Row

LENGTH_TO_ANG = {
    "ang": 1.0,
    "a0": 0.529177210903,
    "bohr": 0.529177210903,
    "nm": 10.0,
    "m": 1e10,
    "cm": 1e8,
}

@dataclass(frozen=True)
class BlockIssue:
    line: int
    col: int
    end_col: int
    message: str


def _row_issue(row: Row, message: str) -> BlockIssue:
    return BlockIssue(row.line, row.col, row.end_col, message)


def _block_issue(block: Block, message: str) -> BlockIssue:
    return BlockIssue(block.line, block.col, block.end_col, message)


def _unit_line(rows: list[Row]) -> bool:
    return bool(rows) and len(rows[0].text.split()) == 1 and not is_real(rows[0].text)


def parse_lattice(block: Block) -> tuple[Lattice | None, list[BlockIssue]]:
    """Parse ``lattice_cart`` (3 vectors) or ``lattice_abc`` (a b c / alpha beta gamma)."""
    rows = list(block.rows)
    issues: list[BlockIssue] = []
    scale = 1.0
    if _unit_line(rows):
        unit = rows[0].text.lower()
        if unit in LENGTH_TO_ANG:
            scale = LENGTH_TO_ANG[unit]
        else:
            issues.append(
                _row_issue(
                    rows[0],
                    f"Unknown length unit '{rows[0].text}'. Use one of: {', '.join(sorted(LENGTH_TO_ANG))}.",
                )
            )
        rows = rows[1:]

    cart = block.key == "lattice_cart"
    expected = 3 if cart else 2
    if len(rows) != expected:
        issues.append(
            _block_issue(
                block,
                f"{block.raw_key} needs {expected} data lines"
                f"{' (one per lattice vector)' if cart else ' (a b c, then alpha beta gamma)'}; found {len(rows)}.",
            )
        )
    numeric: list[list[float]] = []
    for row in rows:
        tokens = row.text.split()
        if len(tokens) != 3 or not all(is_real(t) for t in tokens):
            issues.append(_row_issue(row, "Expected three numbers on this line."))
        else:
            numeric.append([to_float(t) for t in tokens])
    if issues:
        return None, issues

    if cart:
        lattice = Lattice([[x * scale for x in row] for row in numeric])
    else:
        a, b, c = (x * scale for x in numeric[0])
        lattice = lattice_from_abc(a, b, c, *numeric[1])
        if lattice is None:
            return None, [_block_issue(block, "The cell lengths/angles do not describe a valid cell.")]
    if abs(lattice.volume) < 1e-8:
        return None, [_block_issue(block, "The lattice vectors are linearly dependent (zero cell volume).")]
    return lattice, []


@dataclass(frozen=True)
class Atom:
    label: str
    coords: tuple[float, float, float]  # fractional, or angstrom for positions_abs
    frac: bool
    spin: float | None
    row: Row


def parse_positions(block: Block, frac: bool) -> tuple[list[Atom], list[BlockIssue]]:
    atoms: list[Atom] = []
    issues: list[BlockIssue] = []
    rows = list(block.rows)
    scale = 1.0
    if _unit_line(rows):
        unit = rows[0].text.lower()
        if frac:
            issues.append(_row_issue(rows[0], "A unit line is not allowed in a fractional positions block."))
        elif unit in LENGTH_TO_ANG:
            scale = LENGTH_TO_ANG[unit]
        else:
            issues.append(_row_issue(rows[0], f"Unknown length unit '{rows[0].text}'."))
        rows = rows[1:]

    for row in rows:
        parts = row.text.split(None, 4)
        if len(parts) < 4 or not all(is_real(p) for p in parts[1:4]):
            issues.append(_row_issue(row, "Expected 'species x y z' with three numeric coordinates."))
            continue
        coords = tuple(to_float(p) * (1.0 if frac else scale) for p in parts[1:4])
        spin = None
        if len(parts) == 5:
            # Trailing options (spin=, mixture:(), label=, fix_spin) are not validated, only spin= is read.
            if m := re.search(r"\bspin\s*=\s*(\S+)", parts[4], re.I):
                spin = to_float(m[1])
                if spin is None:
                    issues.append(_row_issue(row, f"spin value '{m[1]}' is not a number."))
        atoms.append(Atom(parts[0], coords, frac, spin, row))  # type: ignore[arg-type]
    return atoms, issues


DEFAULT_POT = "*"


@dataclass(frozen=True)
class PotEntry:
    label: str
    pot: str
    row: Row


def parse_species_pot(block: Block) -> tuple[dict[str, PotEntry], list[BlockIssue]]:
    entries: dict[str, PotEntry] = {}
    issues: list[BlockIssue] = []
    for row in block.rows:
        tokens = row.text.split()
        if len(tokens) == 1:  # a library name that applies to every species
            entries.setdefault(DEFAULT_POT, PotEntry(DEFAULT_POT, tokens[0], row))
        elif len(tokens) == 2:
            entries.setdefault(tokens[0].lower(), PotEntry(tokens[0], tokens[1], row))
        else:
            issues.append(_row_issue(row, "Expected 'species pseudopotential', or a single library name."))
    return entries, issues


def check_kpoint_list(block: Block) -> list[BlockIssue]:
    issues: list[BlockIssue] = []
    for row in block.rows:
        tokens = row.text.split()
        if len(tokens) < 3 or not all(is_real(t) for t in tokens[:3]):
            issues.append(_row_issue(row, "Expected 'kx ky kz [weight] [label]' with three numeric coordinates."))
    return issues
