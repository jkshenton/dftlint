"""Line-aware tokenizer for CASTEP .cell and .param files.

castep_outputs gives structured values but no positions, drops bare keywords and
raises position-less exceptions, so the linter keeps its own tokenizer. Every
node here carries 1-based line/column spans so diagnostics can point at the
exact text. ``end_col`` is exclusive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

COMMENT_CHARS = "#!;"

_BLOCK_START = re.compile(r"^(\s*)%\s*block\b\s*(\S*)(.*)$", re.IGNORECASE)
_BLOCK_END = re.compile(r"^(\s*)%\s*endblock\b\s*(\S*)(.*)$", re.IGNORECASE)
_KEY = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(?:\s*[:=]\s*|\s+|$)(.*)$")
_DIRECTIVE = re.compile(
    r"dftlint:\s*(disable-file|disable)\s*=\s*([A-Za-z0-9_,\s]*)", re.IGNORECASE
)


@dataclass
class Row:
    """One data line inside a block (comment stripped)."""

    line: int
    col: int
    end_col: int
    text: str


@dataclass
class Entry:
    """A scalar ``key value`` line."""

    key: str  # lower-case
    raw_key: str
    value: str
    line: int
    col: int
    end_col: int
    vcol: int
    vend: int
    kind: ClassVar[str] = "scalar"


@dataclass
class Block:
    """A ``%BLOCK name`` ... ``%ENDBLOCK name`` region. col/end_col span the name."""

    key: str
    raw_key: str
    line: int
    col: int
    end_col: int
    rows: list[Row] = field(default_factory=list)
    end_line: int | None = None
    kind: ClassVar[str] = "block"


@dataclass(frozen=True)
class SyntaxIssue:
    line: int
    col: int
    end_col: int
    message: str


@dataclass
class ParsedFile:
    path: str
    kind: str  # "cell" or "param"
    lines: list[str]
    entries: list[Entry] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    syntax_errors: list[SyntaxIssue] = field(default_factory=list)
    suppress_lines: dict[int, set[str]] = field(default_factory=dict)
    suppress_file: set[str] = field(default_factory=set)

    def nodes(self) -> list[Entry | Block]:
        """Scalars and blocks in file order (unnamed blocks excluded)."""
        found = [n for n in (*self.entries, *self.blocks) if n.key]
        return sorted(found, key=lambda n: (n.line, n.col))

    def is_suppressed(self, rule_id: str, line: int) -> bool:
        def hit(codes: set[str]) -> bool:
            return any(c == "ALL" or rule_id.startswith(c) for c in codes)

        return hit(self.suppress_file) or hit(self.suppress_lines.get(line, set()))


def _split_comment(raw: str) -> tuple[str, str]:
    positions = [i for i in (raw.find(c) for c in COMMENT_CHARS) if i >= 0]
    if not positions:
        return raw, ""
    idx = min(positions)
    return raw[:idx], raw[idx + 1 :]


def parse_text(text: str, path: str, kind: str) -> ParsedFile:
    lines = text.splitlines()
    pf = ParsedFile(path=path, kind=kind, lines=lines)
    block: Block | None = None
    pending: set[str] = set()

    def issue(line: int, col: int, end_col: int, message: str) -> None:
        pf.syntax_errors.append(SyntaxIssue(line, col, end_col, message))

    for no, raw in enumerate(lines, 1):
        code, comment = _split_comment(raw)
        stripped = code.strip()

        codes: set[str] = set()
        directive = _DIRECTIVE.search(comment) if comment else None
        if directive:
            codes = {c.strip().upper() for c in directive.group(2).split(",") if c.strip()}
            if directive.group(1).lower() == "disable-file":
                pf.suppress_file |= codes
                codes = set()

        if not stripped:
            if codes:  # a directive on a comment-only line applies to the next code line
                pending |= codes
            continue

        applies = codes | pending
        if applies:
            pf.suppress_lines.setdefault(no, set()).update(applies)
        pending = set()

        col = len(code) - len(code.lstrip()) + 1
        end_of_code = len(code.rstrip()) + 1

        m_start = _BLOCK_START.match(code)
        if m_start:
            name = m_start.group(2)
            ncol = m_start.start(2) + 1
            if block is not None:
                issue(
                    block.line,
                    block.col,
                    block.end_col,
                    f"Block '{block.raw_key}' is not closed with %ENDBLOCK before "
                    f"'%BLOCK {name}' at line {no}.",
                )
                pf.blocks.append(block)
            if not name:
                issue(no, col, end_of_code, "%BLOCK is missing a block name.")
            elif m_start.group(3).strip():
                issue(no, m_start.start(3) + 1, end_of_code, "Unexpected text after the block name.")
            block = Block(name.lower(), name, no, ncol, ncol + len(name))
            continue

        m_end = _BLOCK_END.match(code)
        if m_end:
            name = m_end.group(2)
            if block is None:
                issue(no, col, end_of_code, "%ENDBLOCK without a matching %BLOCK.")
            else:
                if name and name.lower() != block.key:
                    issue(
                        no,
                        m_end.start(2) + 1,
                        m_end.start(2) + 1 + len(name),
                        f"%ENDBLOCK '{name}' does not match the open block "
                        f"'{block.raw_key}' (line {block.line}).",
                    )
                block.end_line = no
                pf.blocks.append(block)
                block = None
            continue

        if block is not None:
            block.rows.append(Row(no, col, end_of_code, stripped))
            continue

        m = _KEY.match(code)
        if not m:
            issue(no, col, end_of_code, "Cannot parse this line as 'keyword value'.")
            continue
        key = m.group(2)
        value = m.group(3).strip()
        vcol = m.start(3) + 1
        vend = vcol + len(value) if value else m.end(2) + 1
        pf.entries.append(
            Entry(key.lower(), key, value, no, m.start(2) + 1, m.end(2) + 1, vcol, vend)
        )

    if block is not None:
        issue(block.line, block.col, block.end_col, f"Block '{block.raw_key}' is never closed with %ENDBLOCK.")
        pf.blocks.append(block)
    return pf
