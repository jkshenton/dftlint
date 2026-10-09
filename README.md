# dftlint

A standalone, rule-based linter for DFT input files. It reads your inputs, applies
simple checks, and reports problems with file, line and column, with no need to run
the DFT code. Think of it as `castep -dryrun` that is independent of CASTEP and runs
in milliseconds. CASTEP (`.cell` / `.param`) is supported first; the core is
code-agnostic so VASP and others can be added as plugins.

```console
$ dftlint examples/broken.cell
examples/broken.cell:18:1: E001 error Unknown keyword 'kpoints_mp_spacng'. Did you mean 'kpoints_mp_spacing'?
examples/broken.cell:20:1: E012 error kpoints_mp_spacing conflicts with kpoints_mp_grid (line 19); give only one k-point specification.
examples/broken.param:5:20: E002 error 'maybe' is not a boolean for spin_polarised; use true or false.
...
```

## Install and run

```bash
pip install -e '.[dev]'          # add ',crosscheck' for the castep_outputs cross-check
dftlint seed.cell                # also lints seed.param if it sits next to it
dftlint path/to/calcs/           # recurses; pairs seed.cell with seed.param
dftlint seed.cell --format json
dftlint --list-rules
dftlint --explain W031
```

Exit status: `0` clean (or only warnings/info), `1` errors found (`--strict`: warnings
too), `2` usage or config error.

## Rules

| Id | Sev | What it catches |
|----|-----|-----------------|
| E001 | error | Unknown keyword (with a "did you mean" suggestion) |
| E002 | error | Invalid value: type, enum, range or unit |
| E003 | error | Duplicate keyword (aliases count as the same keyword) |
| E004 | error | Syntax: unclosed, nested or mismatched `%BLOCK`/`%ENDBLOCK`, unparsable line |
| E005 | error | Block keyword written as a scalar, or scalar written as a block |
| E011 | error | `positions_frac` and `positions_abs` both given |
| E012 | error | More than one way of specifying a k-point set (list / grid / spacing / path) |
| E013 | error | Species label is not an element symbol, atomic number or `H:1` style label |
| E014 | error | No lattice or no atomic positions in the `.cell` |
| E015 | error | `lattice_cart` and `lattice_abc` both given |
| E016 | error | Malformed block rows: lattice, positions, species_pot, k-point lists, zero cell volume |
| E017 | error | `cut_off_energy` and `basis_precision` both set (CASTEP aborts) |
| W010 | warn | Atom species missing from an existing `species_pot` block |
| W011 | warn | `species_pot` entry for a species that is not in the cell |
| W020 | warn | `geom_*` / `md_*` keywords that the selected task ignores |
| W021 | warn | `spin=` on atoms but spin polarisation not enabled in the `.param` |
| W022 | warn | `task=bandstructure` without `bs_kpoint_*` |
| W023 | warn | Geometry optimisation with `fix_all_ions` and `fix_all_cell` both true |
| W030 | warn | `cut_off_energy` below threshold (units converted) |
| W031 | warn | k-point spacing, or spacing implied by an MP grid and the lattice, too coarse |
| W032 | warn | `smearing_width`/`smearing_scheme` with `fix_occupancy : true` (CASTEP ignores them) |
| W040 | warn | Atoms closer than a minimum distance (minimum image, Minkowski-reduced cell) |
| W041 | warn | `snap_to_symmetry` without `symmetry_generate`/`symmetry_ops` |
| W050 | warn | Obsolete keyword (with the replacement, e.g. `bs_kpoint_*` -> `spectral_kpoint_*`) |
| W060 | warn | `--cross-check` only: `castep_outputs` cannot parse the file |
| I050 | info | Alias spelling used (e.g. `kpoint_mp_spacing`) |

Cross-file rules (W021, W022, W023) need both files; `dftlint seed.cell` picks up
`seed.param` automatically.

## Configuration

`.dftlint.toml`, found by searching upwards from the first path:

```toml
ignore = ["I", "W031"]            # ids or prefixes
# select = ["E"]                  # only these
extra_keywords = ["my_new_keyword"]
cross_check = false

[options]
min_cutoff_ev = 300               # W030 (default 250)
max_kspacing = 0.07               # W031, 1/angstrom (default 0.1)
min_distance = 0.8                # W040, angstrom (default 0.5)
```

Suppress in the file itself, ruff-style:

```
cut_of_energy : 5      # dftlint: disable=E001
# dftlint: disable=W030,I      <- applies to the next code line
# dftlint: disable-file=W031   <- whole file
```

## Layout

```
src/dftlint/
  core.py            Diagnostic, rule registry, config
  runner.py          file collection, seed grouping, plugin dispatch
  report.py          text / json
  cli.py
  castep/
    parse.py         line-aware tokenizer (positions on every node)
    blocks.py        lattice / positions / species_pot / k-point-list parsing
    schema.py        schema loader + value validation
    schema/*.yaml    keyword schemas (data, not code)
    rules.py         the rules
    plugin.py        CASTEP plugin (a VASP plugin would sit beside it)
```

New rule: write a function decorated with `@rule("W0xx", "warning", "summary")` taking a
`Context`; its docstring becomes the `--explain` text. New keyword: add a line to
`schema/cell.yaml` or `schema/param.yaml`.

## Why not build on castep_outputs?

It was evaluated first (v0.3.3). It is excellent for reading outputs, but for linting it
has no line numbers, silently drops bare keywords (`symmetry_generate` disappears from
the parsed `.cell`), and raises position-less exceptions on unterminated blocks. So
dftlint has its own small tokenizer, and uses `castep_outputs` only as an optional second
opinion (`--cross-check`).

## Keyword schema

`src/dftlint/castep/schema/*.yaml` is generated from `castep -help` by `tools/gen_schema.py`
(run it against your CASTEP; takes about a minute). Behaviour the schema alone can't give is
taken from the CASTEP source and checked with `castep -dryrun`: keyword matching ignores
underscores and case, numbers in blocks may be arithmetic (`1/3`, `cos(60)*2.9`), and some
enums accept spellings that `-help` does not list (`ENUM_EXTRA` in the generator).
The raw scrape (`tools/castep_help_raw.json`) is not committed.

## Known limitations

* The schema reflects the CASTEP version it was generated from. Hidden keywords that
  `-help` does not list (e.g. `implicit_solvent_surface_tension`) give a false E001; list
  them under `extra_keywords`.
* Enum values come from `-help`, but CASTEP parses each enum by hand, so a few valid
  spellings are missing. Report them and add to `ENUM_EXTRA`.
* W041 (`snap_to_symmetry` without symmetry operations) encodes believed behaviour;
  `castep -dryrun` accepts it silently.
* W031's MP-grid check uses `|b_i|/n_i` with reciprocal vectors without 2π, matching
  `cell_kpoints_spacing_wrapped` in the CASTEP source.
* W031 fires on the deliberately coarse grids in CASTEP's own test suite; that is expected.
* No LSP server or VS Code extension yet. Diagnostics already carry line, column and
  end column, so `pygls` can wrap the existing runner (see `lint_paths` in `runner.py`).
