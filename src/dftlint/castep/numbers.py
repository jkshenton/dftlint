"""Numeric helpers for CASTEP-style tokens.

CASTEP evaluates simple arithmetic inside a single whitespace-free word (io_parse_arithmetic
in io.F90): ``+ - * /`` with normal precedence, and ``sqrt() sin() cos() tan()`` (degrees).
Brackets other than function arguments are not supported. Fortran ``d`` exponents are accepted.
"""

from __future__ import annotations

import math
import re

_NUM = r"(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?"
_REAL = re.compile(rf"^[+-]?{_NUM}$")
_INT = re.compile(r"^[+-]?\d+$")
_TOKEN = re.compile(rf"{_NUM}|[-+*/]")
_FUNC = re.compile(r"(sqrt|sin|cos|tan)\(([^()]*)\)")
_FUNCS = {
    "sqrt": math.sqrt,
    "sin": lambda x: math.sin(math.radians(x)),
    "cos": lambda x: math.cos(math.radians(x)),
    "tan": lambda x: math.tan(math.radians(x)),
}


def _plain(token: str) -> float:
    return float(token.replace("d", "e").replace("D", "E"))


def _evaluate(text: str) -> float | None:
    """Evaluate numbers joined by + - * / (no brackets)."""
    tokens = _TOKEN.findall(text)
    if "".join(tokens) != text:
        return None
    pos = 0

    def number() -> float | None:
        nonlocal pos
        sign = 1.0
        while pos < len(tokens) and tokens[pos] in "+-":
            sign = -sign if tokens[pos] == "-" else sign
            pos += 1
        if pos >= len(tokens) or tokens[pos] in "+-*/":
            return None
        pos += 1
        return sign * _plain(tokens[pos - 1])

    def term() -> float | None:
        nonlocal pos
        value = number()
        while value is not None and pos < len(tokens) and tokens[pos] in "*/":
            op = tokens[pos]
            pos += 1
            rhs = number()
            if rhs is None or (op == "/" and rhs == 0):
                return None
            value = value * rhs if op == "*" else value / rhs
        return value

    value = term()
    while value is not None and pos < len(tokens) and tokens[pos] in "+-":
        op = tokens[pos]
        pos += 1
        rhs = term()
        value = None if rhs is None else value + rhs if op == "+" else value - rhs
    return value if pos == len(tokens) else None


def to_float(token: str) -> float | None:
    """Value of a number or arithmetic word, or ``None`` if it is neither."""
    if _REAL.match(token):
        return _plain(token)
    while (m := _FUNC.search(token)) is not None:
        arg = _evaluate(m[2])
        if arg is None:
            return None
        try:
            result = _FUNCS[m[1]](arg)
        except ValueError:
            return None
        token = f"{token[:m.start()]}{result!r}{token[m.end():]}"
    return _evaluate(token)


def is_real(token: str) -> bool:
    return to_float(token) is not None


def is_int(token: str) -> bool:
    return bool(_INT.match(token))
