"""Regenerate src/dftlint/castep/schema/*.yaml from a CASTEP binary's built-in help.

    python tools/gen_schema.py [castep-command]      (default: castep, ~1 minute)
    python tools/gen_schema.py --from-raw            (redo the conversion without CASTEP)

Scrapes `castep -help all` for the keyword list (the section header says whether a
keyword is .cell or .param, and aliases/obsolete are marked in the description), then
`castep -help <keyword>` for type, allowed values and default. The raw scrape is kept
in `tools/castep_help_raw.json` so the conversion can be re-run without CASTEP.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = Path(__file__).with_name("castep_help_raw.json")
SCHEMA_DIR = ROOT / "src" / "dftlint" / "castep" / "schema"


def run_help(cmd: str, what: str) -> str:
    out = subprocess.run([cmd, "-help", what], capture_output=True, text=True, check=True)
    return out.stdout


def list_keywords(help_all: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {"cell": {}, "param": {}}
    current = None
    for line in help_all.splitlines():
        if line.startswith("Help information on CELL"):
            current = "cell"
        elif line.startswith("Help information on PARAMETERS"):
            current = "param"
        elif current and (m := re.match(r"^([A-Z0-9_]+)\s{2,}(.*)$", line)):
            sections[current][m[1].lower()] = m[2].strip()
    return sections


def parse_detail(text: str, kind: str) -> dict[str, str]:
    """Pull Type/Level/Allowed/Default out of one `-help` page, for the right section."""
    marker = "Help information on CELL keywords:" if kind == "cell" else "Help information on PARAMETERS keywords:"
    body = text.split(marker, 1)[1]
    body = re.split(r"Help information on (?:CELL|PARAMETERS) keywords:", body)[0]
    detail: dict[str, str] = {}
    if m := re.search(r"^Type: (.+?)\s+Level: (\S+)", body, re.M):
        detail["type"], detail["level"] = m[1], m[2]
    if m := re.search(r"^Allowed values: (.*)$", body, re.M):
        detail["allowed"] = m[1].strip()
    if m := re.search(r"^Default value ?: (.*)$", body, re.M):
        detail["default"] = m[1].strip()
    if re.search(r"^Also allowed", body, re.M):
        detail["also_allowed"] = True
    if m := re.search(r"replaced by (\w+)", body):
        detail["replaced_by"] = m[1].lower()
    return detail


def scrape(cmd: str) -> dict:
    sections = list_keywords(run_help(cmd, "all"))
    jobs = [(kind, kw) for kind, kws in sections.items() for kw, desc in kws.items() if not desc.startswith(("Alias for", "(OBSOLETE) Alias for"))]
    with ThreadPoolExecutor(4) as pool:
        pages = list(pool.map(lambda j: run_help(cmd, j[1]), jobs))
    raw: dict = {"cell": {}, "param": {}}
    for kind, kws in sections.items():
        for kw, desc in kws.items():
            raw[kind][kw] = {"desc": desc}
    for (kind, kw), page in zip(jobs, pages):
        raw[kind][kw].update(parse_detail(page, kind))
    return raw


# The help text has no unit class; take it from the default value ("0.1 1/ang") or, failing
# that, from this table.
UNIT_CLASS_BY_TOKEN = {
    "ev": "energy", "ev/atom": "energy", "ha": "energy", "ry": "energy", "thz": "energy", "cm-1": "energy",
    "ang": "length", "angstroms": "length", "bohr": "length", "ps": "time", "fs": "time",
    "k": "temperature", "gpa": "pressure", "1/ang": "inv_length", "/ang": "inv_length",
    "ev/ang": "force",
}
UNIT_CLASS_OVERRIDES = {
    "cut_off_energy": "energy", "fine_cut_off_energy": "energy", "mix_cut_off_energy": "energy",
    "bs_eigenvalue_tol": "energy", "dfpt_eigenvalue_tol": "energy", "elnes_eigenvalue_tol": "energy",
    "md_langevin_t": "time", "md_nose_t": "time", "wannier_ion_rmax": "length",
}
# Values CASTEP accepts that `-help` does not list (parameters.f90 / the keyword's reader).
ENUM_EXTRA = {
    "task": ["bandstructure", "optics", "elnes", "wannier", "geometryoptimisation", "geomopt", "geom", "spe",
             "energy", "md", "dynamics", "spectral", "epc", "epme", "socket_driver", "elastics",
             "elastic_constants", "elastic_constant", "elasticconstants", "elasticconstant", "koopman", "socketdriver"],
    "phonon_fine_method": ["interpolation"],
}
_NUM = r"[-+0-9.eEdD*^]+"


def unit_class(name: str, detail: dict) -> str | None:
    if name in UNIT_CLASS_OVERRIDES:
        return UNIT_CLASS_OVERRIDES[name]
    if name.endswith("_spacing") and name.split("_")[-2] in ("mp", "path"):
        return "inv_length"
    if m := re.match(rf"^{_NUM}\s+(\S+?)\.?$", detail.get("default", "")):
        return UNIT_CLASS_BY_TOKEN.get(m[1].lower())
    return None


def convert_one(name: str, detail: dict, kind: str) -> dict:
    out: dict = {}
    t, allowed = detail.get("type"), detail.get("allowed", "")
    if t == "Block":
        out["kind"] = "block"
    elif t == "Boolean (Logical)" or (kind == "cell" and name in ("symmetry_generate", "snap_to_symmetry")):
        out["type"] = "bool"
    elif t == "Integer":
        out["type"] = "int"
    elif t == "Real":
        out["type"] = "real"
    elif t == "Physical":
        out["type"] = "real_unit"
        if uc := unit_class(name, detail):
            out["unit_class"] = uc
    elif t == "Integer Vector":
        out["type"] = "int3"
    elif t == "Real Vector":
        out["type"] = "real3"
    elif t == "Defined":
        out["kind"], out["type"] = "any", "any"
    elif t == "String" and not detail.get("also_allowed") and re.fullmatch(r"[\w+\-.]+(, [\w+\-.]+)+", allowed):
        out["type"], out["values"] = "enum", [v.lower() for v in allowed.split(", ")] + ENUM_EXTRA.get(name, [])
    if out.get("type") in ("int", "real", "real_unit"):
        if m := re.fullmatch(rf"\(any(?: integer)?\) *>=? *({_NUM})", allowed):
            try:
                out["min"] = float(m[1]) if out["type"] != "int" else int(float(m[1]))
            except ValueError:
                pass
    if detail["desc"].startswith("(OBSOLETE)"):
        out["obsolete"] = detail.get("replaced_by", True)
    return out


def convert(raw: dict, kind: str) -> dict:
    aliases: dict[str, list[str]] = {}
    for name, d in raw[kind].items():
        if m := re.search(r"Alias for (\w+)", d["desc"]):
            aliases.setdefault(m[1].lower(), []).append(name)
    keywords = {}
    for name, d in raw[kind].items():
        if "type" not in d:
            continue  # alias, listed under its target
        spec = convert_one(name, d, kind)
        if name in aliases:
            spec["aliases"] = sorted(aliases[name])
        keywords[name] = spec
    return keywords


HEADER = """# Keyword schema for CASTEP .{kind} files. GENERATED by tools/gen_schema.py from `castep -help`.
# Do not edit by hand: change the generator (or its override tables) and rerun it.
#
# Fields: kind (scalar|block|any), type (bool|int|real|real_unit|int3|real3|enum|string|any),
# values (enum), unit_class, aliases, min, max, obsolete (true, or the replacement keyword).
"""


def write_yaml(raw: dict) -> None:
    for kind in ("cell", "param"):
        body = yaml.safe_dump({"keywords": convert(raw, kind)}, default_flow_style=None, sort_keys=False, width=10**6)
        (SCHEMA_DIR / f"{kind}.yaml").write_text(HEADER.format(kind=kind) + body)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--from-raw"]:
        write_yaml(json.loads(RAW.read_text()))
    else:
        RAW.write_text(json.dumps(scrape(args[0] if args else "castep"), indent=1, sort_keys=True))
        write_yaml(json.loads(RAW.read_text()))
