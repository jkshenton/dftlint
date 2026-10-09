"""CASTEP lint rules. Each rule is a function taking a Context and reporting findings.

Rule ids: E = error (CASTEP would very likely fail or the file is malformed),
W = warning (legal but suspicious), I = informational.
"""

from __future__ import annotations

import difflib

from ..core import register_meta_rule, rule
from .blocks import DEFAULT_POT, check_kpoint_list
from .context import Context, FileView
from .elements import is_valid_species_label
from .geometry import closest_pair, implied_kspacing
from .parse import Block, Entry
from .numbers import to_float
from .schema import check_value, to_ev, to_inv_ang

# k-point families and the mutually exclusive ways of specifying each one.
KPOINT_FAMILIES = {
    "kpoints": ("list", "mp_grid", "mp_spacing"),
    "bs_kpoint": ("list", "path", "mp_grid", "mp_spacing"),
    "phonon_kpoint": ("list", "path", "mp_grid", "mp_spacing"),
    "phonon_fine_kpoint": ("list", "path", "mp_grid", "mp_spacing"),
    "spectral_kpoint": ("list", "path", "mp_grid", "mp_spacing"),
    "optics_kpoint": ("list", "mp_grid", "mp_spacing"),
    "magres_kpoint": ("list", "mp_grid", "mp_spacing"),
    "supercell_kpoint": ("list", "mp_grid", "mp_spacing"),
}

# Spellings CASTEP accepts for `task` besides the canonical ones (parameters.f90, parameters_read).
_TASK_SYNONYMS = {
    "geometryoptimisation": "geometryoptimization", "geom": "geometryoptimization",
    "geomopt": "geometryoptimization", "spe": "singlepoint", "energy": "singlepoint",
    "md": "moleculardynamics", "dynamics": "moleculardynamics", "spectral": "electronicspectroscopy",
    "epc": "epcoupling", "epme": "epcoupling", "socket_driver": "socketdriver",
}

# Tasks for which geom_* / md_* keywords have no effect.
_NO_GEOM_TASKS = {"singlepoint", "bandstructure", "optics", "magres", "elnes", "electronicspectroscopy", "efield"}
_NO_MD_TASKS = _NO_GEOM_TASKS | {"phonon", "geometryoptimization"}
_GEOMOPT_TASKS = {"geometryoptimization"}


def _suggest(word: str, choices: list[str]) -> str:
    match = difflib.get_close_matches(word.lower(), choices, n=1, cutoff=0.75)
    return f" Did you mean '{match[0]}'?" if match else ""


def _task(view: FileView) -> str:
    task = (view.value("task") or "singlepoint").replace(" ", "").lower()
    return _TASK_SYNONYMS.get(task, task)


# --------------------------------------------------------------------------- errors


@rule("E001", "error", "Unknown keyword")
def unknown_keywords(ctx: Context) -> None:
    """The keyword is not in dftlint's schema for this file type.

    Usually a typo; a close match is suggested. The bundled schema is hand-compiled
    and incomplete, so for a valid CASTEP keyword that is missing, list it under
    `extra_keywords` in the dftlint config.
    """
    for view in ctx.views():
        names = view.schema.names()
        for node in view.pf.nodes():
            if view.schema.lookup(node.key) is None:
                ctx.report_node(
                    "E001", view, node, f"Unknown keyword '{node.raw_key}'." + _suggest(node.key, names)
                )


@rule("E002", "error", "Invalid value")
def invalid_values(ctx: Context) -> None:
    """The value does not match the keyword's type, allowed values, range or units."""
    for view in ctx.views():
        for node in view.pf.entries:
            spec = view.schema.lookup(node.key)
            if spec is None or spec.kind == "block":
                continue
            message = check_value(spec, node.value, view.kind)
            if message:
                if node.value:
                    ctx.report("E002", view, node.line, node.vcol, node.vend, message)
                else:
                    ctx.report_node("E002", view, node, message)


@rule("E003", "error", "Duplicate keyword")
def duplicate_keywords(ctx: Context) -> None:
    """A keyword (or an alias of it) is defined more than once in the same file."""
    for view in ctx.views():
        seen: dict[str, Entry | Block] = {}
        for node in view.pf.nodes():
            canon = view.schema.canonical(node.key) or node.key
            first = seen.setdefault(canon, node)
            if first is not node:
                same = "" if first.key == node.key else f" (as '{first.raw_key}')"
                ctx.report_node(
                    "E003", view, node,
                    f"'{node.raw_key}' is already defined at line {first.line}{same}.",
                )


@rule("E004", "error", "Syntax error")
def syntax_errors(ctx: Context) -> None:
    """Structural problems: unclosed or mismatched %BLOCK/%ENDBLOCK, unparsable lines."""
    for view in ctx.views():
        for issue in view.pf.syntax_errors:
            ctx.report("E004", view, issue.line, issue.col, issue.end_col, issue.message)


@rule("E005", "error", "Block/scalar mismatch")
def block_scalar_mismatch(ctx: Context) -> None:
    """A block keyword is written as a single line, or a scalar keyword as a %BLOCK."""
    for view in ctx.views():
        for node in view.pf.nodes():
            spec = view.schema.lookup(node.key)
            if spec is None:
                continue
            if isinstance(node, Entry) and spec.kind == "block":
                ctx.report_node(
                    "E005", view, node,
                    f"'{node.raw_key}' is a block keyword: write %BLOCK {node.raw_key} ... %ENDBLOCK {node.raw_key}.",
                )
            elif isinstance(node, Block) and spec.kind == "scalar":
                ctx.report_node(
                    "E005", view, node, f"'{node.raw_key}' is a single-line keyword, not a block."
                )


@rule("E011", "error", "positions_frac and positions_abs both given")
def positions_conflict(ctx: Context) -> None:
    """Atomic positions must be given either fractionally or in absolute coordinates, not both."""
    view = ctx.cell
    if view is None:
        return
    frac, absolute = view.nodes_named("positions_frac"), view.nodes_named("positions_abs")
    if frac and absolute:
        later = max(frac[0], absolute[0], key=lambda n: n.line)
        ctx.report_node("E011", view, later, "positions_frac and positions_abs are both defined; use only one.")


@rule("E012", "error", "Conflicting k-point specifications")
def kpoint_conflicts(ctx: Context) -> None:
    """A k-point set is specified in more than one way (list, MP grid, MP spacing or path)."""
    view = ctx.cell
    if view is None:
        return
    for prefix, variants in KPOINT_FAMILIES.items():
        found = []
        for variant in variants:
            node = view.first(f"{prefix}_{variant}")
            if node is not None:
                found.append((node, f"{prefix}_{variant}"))
        found.sort(key=lambda item: item[0].line)
        for node, name in found[1:]:
            first_node, first_name = found[0]
            ctx.report_node(
                "E012", view, node,
                f"{name} conflicts with {first_name} (line {first_node.line}); give only one k-point specification.",
            )


@rule("E013", "error", "Unknown element in species label")
def unknown_elements(ctx: Context) -> None:
    """A species label is neither an element symbol, an atomic number, nor a suffixed form like 'H:1'."""
    view = ctx.cell
    if view is None:
        return
    reported: set[str] = set()
    for atom in view.positions[0]:
        key = atom.label.lower()
        if key in reported:
            continue
        reported.add(key)
        if not is_valid_species_label(atom.label):
            ctx.report_row("E013", view, atom.row, f"'{atom.label}' is not a recognised element symbol or atomic number.")


@rule("E014", "error", "Missing lattice or positions")
def missing_structure(ctx: Context) -> None:
    """A .cell file needs a lattice (lattice_cart or lattice_abc) and atomic positions."""
    view = ctx.cell
    if view is None:
        return
    if not view.has("lattice_cart", "lattice_abc"):
        ctx.report("E014", view, 1, 1, None, "No lattice defined: add a lattice_cart or lattice_abc block.")
    if not view.has("positions_frac", "positions_abs"):
        ctx.report("E014", view, 1, 1, None, "No atomic positions: add a positions_frac or positions_abs block.")


@rule("E015", "error", "lattice_cart and lattice_abc both given")
def lattice_conflict(ctx: Context) -> None:
    """The lattice must be defined once: either lattice_cart or lattice_abc."""
    view = ctx.cell
    if view is None:
        return
    cart, abc = view.nodes_named("lattice_cart"), view.nodes_named("lattice_abc")
    if cart and abc:
        later = max(cart[0], abc[0], key=lambda n: n.line)
        ctx.report_node("E015", view, later, "lattice_cart and lattice_abc are both defined; use only one.")


@rule("E016", "error", "Malformed block data")
def malformed_blocks(ctx: Context) -> None:
    """Block contents do not have the expected shape.

    Checks lattice vectors (3x3 numbers, or a b c / alpha beta gamma, valid cell),
    position rows ('species x y z [spin=..]'), species_pot rows ('species potential')
    and k-point list rows ('kx ky kz [weight]').
    """
    view = ctx.cell
    if view is None:
        return
    for issue in view.lattice[1] + view.positions[1] + view.species_pot[1]:
        ctx.report_issue("E016", view, issue)
    for prefix in KPOINT_FAMILIES:
        node = view.first(f"{prefix}_list")
        if isinstance(node, Block):
            for issue in check_kpoint_list(node):
                ctx.report_issue("E016", view, issue)


# ------------------------------------------------------------------------- warnings


@rule("W010", "warning", "Species without a species_pot entry")
def species_without_pot(ctx: Context) -> None:
    """A species_pot block exists but an atom's species is missing from it.

    CASTEP falls back to a default pseudopotential for that species, which is easy
    to do by accident when the others are set explicitly.
    """
    view = ctx.cell
    if view is None:
        return
    pots = view.species_pot[0]
    if not pots or DEFAULT_POT in pots:
        return
    reported: set[str] = set()
    for atom in view.positions[0]:
        key = atom.label.lower()
        if key not in pots and key not in reported:
            reported.add(key)
            ctx.report_row(
                "W010", view, atom.row,
                f"Species '{atom.label}' has no species_pot entry; CASTEP will use a default pseudopotential for it.",
            )


@rule("W011", "warning", "species_pot entry without matching atoms")
def unused_pot(ctx: Context) -> None:
    """A species_pot entry names a species that does not occur in the positions block."""
    view = ctx.cell
    if view is None:
        return
    atoms = view.positions[0]
    if not atoms:
        return
    used = {a.label.lower() for a in atoms}
    for key, entry in view.species_pot[0].items():
        if key not in used and key != DEFAULT_POT:
            ctx.report_row("W011", view, entry.row, f"species_pot entry '{entry.label}' matches no species in the positions block.")


@rule("W020", "warning", "Keywords irrelevant for the selected task")
def irrelevant_for_task(ctx: Context) -> None:
    """geom_* keywords with a non-geometry task, or md_* keywords with a non-MD task.

    The task defaults to singlepoint when not set.
    """
    view = ctx.param
    if view is None:
        return
    task = _task(view)
    for node in view.pf.entries:
        if node.key.startswith("geom_") and task in _NO_GEOM_TASKS:
            ctx.report_node("W020", view, node, f"{node.raw_key} has no effect for task={task}.")
        elif node.key.startswith("md_") and task in _NO_MD_TASKS:
            ctx.report_node("W020", view, node, f"{node.raw_key} has no effect for task={task}.")


@rule("W021", "warning", "spin= on atoms without spin polarisation")
def spin_consistency(ctx: Context) -> None:
    """An atom has spin= in the .cell file but the .param file does not enable spin polarisation.

    Polarisation is enabled by spin_polarised/spin_polarized true or by `spin` > 0.
    """
    if ctx.cell is None or ctx.param is None:
        return
    param = ctx.param
    if param.flag("spin_polarised") is True or param.flag("spin_polarized") is True or param.has("spin"):
        return
    spins = [a for a in ctx.cell.positions[0] if a.spin not in (None, 0.0)]
    if spins:
        ctx.report_row(
            "W021", ctx.cell, spins[0].row,
            "spin= is given but spin polarisation is not enabled in the .param file, so initial spins have no effect.",
        )


@rule("W022", "warning", "Band-structure task without band-structure k-points")
def bandstructure_without_kpoints(ctx: Context) -> None:
    """task=bandstructure but the .cell file has no bs_kpoint_* (or its replacement spectral_kpoint_*) specification."""
    if ctx.cell is None or ctx.param is None or _task(ctx.param) != "bandstructure":
        return
    if not any(ctx.cell.has(f"{p}_kpoint_{v}") for p in ("bs", "spectral") for v in ("list", "path", "mp_grid", "mp_spacing")):
        ctx.report_node(
            "W022", ctx.param, ctx.param.first("task"),
            "task=bandstructure but the .cell file defines no bs_kpoint_* list, path or grid.",
        )


@rule("W023", "warning", "Geometry optimisation with everything fixed")
def nothing_to_optimise(ctx: Context) -> None:
    """task=geometryoptimization with both fix_all_ions and fix_all_cell true: nothing can move."""
    if ctx.cell is None or ctx.param is None or _task(ctx.param) not in _GEOMOPT_TASKS:
        return
    if ctx.cell.flag("fix_all_ions") is True and ctx.cell.flag("fix_all_cell") is True:
        later = max(ctx.cell.first("fix_all_ions"), ctx.cell.first("fix_all_cell"), key=lambda n: n.line)
        ctx.report_node(
            "W023", ctx.cell, later,
            "task is a geometry optimisation but fix_all_ions and fix_all_cell are both true: nothing can relax.",
        )


@rule("W030", "warning", "Plane-wave cutoff low")
def cutoff_checks(ctx: Context) -> None:
    """cut_off_energy is below `min_cutoff_ev` (default 250 eV; units are converted).

    A missing cutoff is not reported: CASTEP then derives one from basis_precision (FINE).
    """
    view = ctx.param
    if view is None:
        return
    cutoff = view.first("cut_off_energy")
    if isinstance(cutoff, Entry):
        tokens = cutoff.value.split()
        if tokens and check_value(view.schema.lookup("cut_off_energy"), cutoff.value, "param") is None:
            ev = to_ev(to_float(tokens[0]), tokens[1] if len(tokens) > 1 else None)
            minimum = float(ctx.config.option("min_cutoff_ev", 250.0))
            if ev is not None and ev < minimum:
                ctx.report(
                    "W030", view, cutoff.line, cutoff.vcol, cutoff.vend,
                    f"cut_off_energy of {ev:.0f} eV is low (threshold {minimum:g} eV); check convergence.",
                )


@rule("W031", "warning", "Coarse k-point sampling")
def coarse_kpoints(ctx: Context) -> None:
    """kpoints_mp_spacing, or the spacing implied by kpoints_mp_grid, is coarser than `max_kspacing`.

    The threshold is the option `max_kspacing` in 1/angstrom (default 0.1, CASTEP's own default).
    An MP grid's implied spacing is max_i |b_i|/n_i using reciprocal vectors WITHOUT a
    2*pi factor. CASTEP's documentation does not state its convention, so verify this
    against your CASTEP version before relying on the grid-based check.
    """
    view = ctx.cell
    if view is None:
        return
    limit = float(ctx.config.option("max_kspacing", 0.1))
    spacing = view.first("kpoints_mp_spacing")
    if isinstance(spacing, Entry):
        tokens = spacing.value.split()
        spec = view.schema.lookup("kpoints_mp_spacing")
        if tokens and check_value(spec, spacing.value, "cell") is None:
            value = to_inv_ang(to_float(tokens[0]), tokens[1] if len(tokens) > 1 else None)
            if value is not None and value > limit:
                ctx.report(
                    "W031", view, spacing.line, spacing.vcol, spacing.vend,
                    f"k-point spacing of {value:.3g} 1/Å is coarser than {limit:g}; check convergence.",
                )
    grid = view.first("kpoints_mp_grid")
    lattice = view.lattice[0]
    if isinstance(grid, Entry) and lattice is not None:
        tokens = grid.value.split()
        spec = view.schema.lookup("kpoints_mp_grid")
        if len(tokens) == 3 and check_value(spec, grid.value, "cell") is None and all(int(t) > 0 for t in tokens):
            value = implied_kspacing(lattice, [int(t) for t in tokens])
            if value > limit:
                ctx.report(
                    "W031", view, grid.line, grid.vcol, grid.vend,
                    f"MP grid {' '.join(tokens)} implies a spacing of about {value:.3g} 1/Å, coarser than {limit:g}.",
                )


@rule("W032", "warning", "Smearing settings with fixed occupancies")
def smearing_with_fixed_occupancy(ctx: Context) -> None:
    """smearing_width or smearing_scheme is set but fix_occupancy is true.

    With fix_occupancy CASTEP treats the system as non-metallic and does not smear (checked:
    the .castep header omits the smearing settings). metals_method is deliberately not
    reported: metals_method = DM is also a valid electronic minimiser for insulators.
    """
    view = ctx.param
    if view is None or view.flag("fix_occupancy") is not True:
        return
    for name in ("smearing_width", "smearing_scheme"):
        node = view.first(name)
        if node is not None:
            ctx.report_node(
                "W032", view, node,
                f"{node.raw_key} has no effect because fix_occupancy is true (the system is treated as non-metallic).",
            )


@rule("E017", "error", "cut_off_energy and basis_precision both set")
def cutoff_and_precision(ctx: Context) -> None:
    """CASTEP aborts with 'duplication with keywords BASIS_PRECISION and CUT_OFF_ENERGY'."""
    view = ctx.param
    if view is None:
        return
    if view.has("cut_off_energy") and view.has("basis_precision"):
        node = view.first("basis_precision")
        ctx.report_node(
            "E017", view, node,
            "Both cut_off_energy and basis_precision are set; CASTEP rejects this. Keep only one.",
        )


@rule("W040", "warning", "Atoms closer than the minimum distance")
def close_atoms(ctx: Context) -> None:
    """Two atoms (minimum-image distance, so across periodic boundaries too) are closer than `min_distance`.

    Option: `min_distance` in angstrom (default 0.5). The cell is Minkowski-reduced first,
    so skewed cells are handled. Structures above 600 atoms are skipped (the search is
    quadratic).
    """
    view = ctx.cell
    if view is None:
        return
    atoms, issues = view.positions
    lattice, lattice_issues = view.lattice
    if issues or lattice_issues or lattice is None or len(atoms) < 2:
        return
    if any("mixture" in a.row.text.lower() for a in atoms):  # virtual crystal: atoms share a site
        return
    if len(atoms) > 600:
        return
    reduced = lattice.minkowski_reduced()
    carts = [lattice.to_cart(a.coords) if a.frac else a.coords for a in atoms]
    result = closest_pair([reduced.to_frac(c) for c in carts], reduced)
    minimum = float(ctx.config.option("min_distance", 0.5))
    if result is not None and result[0] < minimum:
        dist, i, j = result
        ctx.report_row(
            "W040", view, atoms[j].row,
            f"Atom {j + 1} ({atoms[j].label}) is only {dist:.3f} Å from atom {i + 1} ({atoms[i].label}) "
            f"(minimum {minimum:g} Å).",
        )


@rule("W041", "warning", "snap_to_symmetry without symmetry operations")
def snap_without_symmetry(ctx: Context) -> None:
    """snap_to_symmetry is true but neither symmetry_generate nor symmetry_ops is given.

    As far as dftlint knows, snapping acts on the detected symmetry operations, so it
    does nothing unless symmetry is generated or supplied. Verify for your version.
    """
    view = ctx.cell
    if view is None or view.flag("snap_to_symmetry") is not True:
        return
    if view.flag("symmetry_generate") is not True and not view.has("symmetry_ops"):
        ctx.report_node(
            "W041", view, view.first("snap_to_symmetry"),
            "snap_to_symmetry has no effect without symmetry_generate or a symmetry_ops block.",
        )


# --------------------------------------------------------------------------- info


@rule("W050", "warning", "Obsolete keyword")
def obsolete_keywords(ctx: Context) -> None:
    """CASTEP's help marks the keyword as obsolete, usually with a replacement."""
    for view in ctx.views():
        for node in view.pf.nodes():
            spec = view.schema.lookup(node.key)
            if spec is not None and spec.obsolete:
                hint = f"; use '{spec.obsolete}' instead" if isinstance(spec.obsolete, str) else ""
                ctx.report_node("W050", view, node, f"'{node.raw_key}' is obsolete{hint}.")


@rule("I050", "info", "Alias spelling used")
def alias_spelling(ctx: Context) -> None:
    """The keyword is spelled with an accepted alias rather than its canonical name."""
    for view in ctx.views():
        for node in view.pf.nodes():
            canon = view.schema.canonical(node.key)
            if canon is not None and canon != node.key:
                ctx.report_node("I050", view, node, f"'{node.raw_key}' is an alias; the canonical spelling is '{canon}'.")


register_meta_rule("E000", "error", "File could not be read", "The file could not be opened or decoded.")
register_meta_rule("E999", "error", "Internal error in a rule", "A rule raised an exception; this is a dftlint bug. The remaining rules still ran.")
register_meta_rule("W060", "warning", "castep_outputs cannot parse the file", "Only with --cross-check: the independent castep_outputs parser rejected the file.")
