"""Keyword schemas (data in YAML) and value validation."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

import yaml

from .numbers import is_int, is_real, to_float

UNITS: dict[str, set[str]] = {
    "energy": {"ev", "mev", "ry", "mry", "ha", "hartree", "mha", "kj/mol", "kcal/mol", "j", "erg", "hz", "mhz", "ghz", "thz", "cm-1", "k"},
    "length": {"ang", "bohr", "a0", "nm", "m", "cm"},
    "time": {"aut", "s", "ms", "us", "ns", "ps", "fs"},
    "temperature": {"k", "ev", "mev", "ha", "hartree", "ry"},
    "force": {"hartree/bohr", "ha/bohr", "ev/ang", "ry/bohr", "n", "dyne"},
    "pressure": {"gpa", "mpa", "pa", "bar", "kbar", "atm", "ev/ang**3", "ha/bohr**3", "ry/bohr**3", "hartree/bohr**3"},
    "inv_length": {"1/ang", "1/bohr", "1/a0", "1/nm", "1/m", "1/cm"},
}

ENERGY_TO_EV = {
    "ev": 1.0,
    "mev": 1e-3,
    "ry": 13.605693122994,
    "mry": 0.013605693123,
    "ha": 27.211386245988,
    "hartree": 27.211386245988,
    "mha": 0.027211386246,
    "kj/mol": 0.010364269656,
    "kcal/mol": 0.043364104,
}

INV_LENGTH_TO_INV_ANG = {
    "1/ang": 1.0,
    "1/bohr": 1 / 0.529177210903,
    "1/a0": 1 / 0.529177210903,
    "1/nm": 0.1,
    "1/m": 1e-10,
    "1/cm": 1e-8,
}

_TRUE = {"true", "t", ".true.", "yes", "y", "on"}
_FALSE = {"false", "f", ".false.", "no", "n", "off"}


def parse_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def to_ev(value: float, unit: str | None) -> float | None:
    """Convert an energy to eV; ``None`` when the unit is not a convertible energy unit."""
    factor = ENERGY_TO_EV.get((unit or "ev").lower())
    return None if factor is None else value * factor


def to_inv_ang(value: float, unit: str | None) -> float | None:
    factor = INV_LENGTH_TO_INV_ANG.get((unit or "1/ang").lower())
    return None if factor is None else value * factor


@dataclass(frozen=True)
class KeywordSpec:
    name: str
    kind: str = "scalar"  # scalar | block | any
    type: str = "string"
    values: tuple[str, ...] = ()
    unit_class: str | None = None
    aliases: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    obsolete: str | bool = False  # True, or the name of the replacement keyword


def _squash(key: str) -> str:
    return key.lower().replace("_", "")


class Schema:
    def __init__(self, kind: str, specs: tuple[KeywordSpec, ...], extra: tuple[str, ...] = ()):
        self.kind = kind
        self._specs = {s.name: s for s in specs}
        for key in extra:
            key = key.lower()
            if key not in self._specs and not any(key in s.aliases for s in specs):
                self._specs[key] = KeywordSpec(key, kind="any", type="any")
        # CASTEP ignores underscores when matching keywords ("cutoff_energy" is "cut_off_energy").
        self._by_squashed = {_squash(s.name): s for s in self._specs.values()}
        self._by_squashed.update({_squash(a): self._specs[s.name] for s in specs for a in s.aliases})

    def lookup(self, key: str) -> KeywordSpec | None:
        return self._by_squashed.get(_squash(key))

    def canonical(self, key: str) -> str | None:
        spec = self.lookup(key)
        return spec.name if spec else None

    def names(self) -> list[str]:
        return sorted(self._specs | {a: None for sp in self._specs.values() for a in sp.aliases})


@lru_cache(maxsize=None)
def _load_specs(kind: str) -> tuple[KeywordSpec, ...]:
    text = (resources.files("dftlint.castep") / "schema" / f"{kind}.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    specs = []
    for name, d in data["keywords"].items():
        d = d or {}
        specs.append(
            KeywordSpec(
                name=name,
                kind=d.get("kind", "scalar"),
                type=d.get("type", "string"),
                values=tuple(str(v).lower() for v in d.get("values", ())),
                unit_class=d.get("unit_class"),
                aliases=tuple(d.get("aliases", ())),
                min=d.get("min"),
                max=d.get("max"),
                obsolete=d.get("obsolete", False),
            )
        )
    return tuple(specs)


def load_schema(kind: str, extra: tuple[str, ...] = ()) -> Schema:
    return Schema(kind, _load_specs(kind), extra)


def _range_message(spec: KeywordSpec, number: float) -> str | None:
    if spec.min is not None and number < spec.min:
        return f"{spec.name} must be at least {spec.min:g} (got {number:g})."
    if spec.max is not None and number > spec.max:
        return f"{spec.name} must be at most {spec.max:g} (got {number:g})."
    return None


def check_value(spec: KeywordSpec, value: str, file_kind: str) -> str | None:
    """Return an error message if ``value`` is invalid for ``spec``, else ``None``."""
    t = spec.type
    v = value.strip()
    if t == "any":
        return None
    if t == "bool":
        if v == "":
            return None if file_kind == "cell" else f"{spec.name} needs a value (true or false)."
        if parse_bool(v) is None:
            return f"'{v}' is not a boolean for {spec.name}; use true or false."
        return None
    if v == "":
        return f"{spec.name} needs a value."
    if t == "string":
        return None
    if t == "int":
        if not is_int(v):
            return f"'{v}' is not an integer (expected for {spec.name})."
        return _range_message(spec, int(v))
    if t == "real":
        if not is_real(v):
            return f"'{v}' is not a number (expected for {spec.name})."
        return _range_message(spec, to_float(v))
    if t == "real_unit":
        tokens = v.split()
        if len(tokens) > 2:
            return f"Expected '{spec.name} value [unit]'."
        if not is_real(tokens[0]):
            return f"'{tokens[0]}' is not a number (expected for {spec.name})."
        if len(tokens) == 2 and spec.unit_class:
            allowed = UNITS[spec.unit_class]
            if tokens[1].lower() not in allowed:
                return (
                    f"'{tokens[1]}' is not a recognised {spec.unit_class} unit for {spec.name}. "
                    f"Known: {', '.join(sorted(allowed))}."
                )
        return _range_message(spec, to_float(tokens[0]))
    if t in ("int3", "real3"):
        tokens = v.split()
        check = is_int if t == "int3" else is_real
        if len(tokens) != 3 or not all(check(x) for x in tokens):
            what = "integers" if t == "int3" else "numbers"
            return f"{spec.name} needs three {what} (got '{v}')."
        return None
    if t == "enum":
        if v.lower().replace(" ", "") in spec.values:
            return None
        close = difflib.get_close_matches(v.lower(), spec.values, n=1, cutoff=0.6)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        return f"'{v}' is not a valid value for {spec.name}. Allowed: {', '.join(spec.values)}.{hint}"
    return None
