"""One test group per rule: a bad input must fire it, with the right position."""

from __future__ import annotations

import pytest

from conftest import GOOD_CELL, GOOD_PARAM, ids
from dftlint.core import Config


def only(diagnostics, rule_id):
    return [d for d in diagnostics if d.rule_id == rule_id]


def test_good_pair_is_clean(lint):
    assert lint() == []


# ------------------------------------------------------------------ errors


def test_e001_unknown_keyword_with_suggestion(lint):
    (d,) = only(lint(cell=GOOD_CELL + "kpoints_mp_spacng 0.05\n"), "E001")
    assert d.line == GOOD_CELL.count("\n") + 1 and d.col == 1
    assert "kpoints_mp_spacing" in d.message
    assert only(lint(param=GOOD_PARAM + "cut_of_energy : 5\n"), "E001")[0].message.endswith("'cut_off_energy'?")


def test_e001_unknown_block_and_extra_keywords(lint):
    from dftlint.core import Config

    cell = GOOD_CELL + "%BLOCK mystery_block\n 1\n%ENDBLOCK mystery_block\n"
    assert "E001" in ids(lint(cell=cell))
    assert "E001" not in ids(lint(cell=cell, config=Config(extra_keywords=("mystery_block",))))


@pytest.mark.parametrize(
    "param_line",
    [
        "spin_polarised : maybe",
        "basis_precision : ultra",
        "cut_off_energy : 500 parsecs",
        "cut_off_energy : fast",
        "nbands : 3.5",
        "elec_energy_tol : -1e-5 eV",
        "cut_off_energy :",
        "task : singlepont",
    ],
)
def test_e002_invalid_values(lint, param_line):
    param = "\n".join(l for l in GOOD_PARAM.splitlines() if l.split(":")[0].strip() != param_line.split(":")[0].strip())
    assert "E002" in ids(lint(param=param + "\n" + param_line + "\n"))


def test_e002_column_points_at_value(lint):
    (d,) = only(lint(param="cut_off_energy : 500 parsecs\n"), "E002")
    assert (d.line, d.col) == (1, 18)


def test_e002_cell_values(lint):
    assert "E002" in ids(lint(cell=GOOD_CELL + "kpoints_mp_grid 4 4\n"))
    assert "E002" not in ids(lint(cell=GOOD_CELL.replace("kpoints_mp_spacing 0.05", "kpoints_mp_grid 4 4 4")))
    assert "E002" not in ids(lint(cell=GOOD_CELL + "symmetry_generate\n"))  # bare bool is valid in .cell


def test_e003_duplicates_including_aliases(lint):
    diags = only(lint(cell=GOOD_CELL + "kpoint_mp_spacing 0.04\n"), "E003")
    assert len(diags) == 1 and "as 'kpoints_mp_spacing'" in diags[0].message
    assert only(lint(param=GOOD_PARAM + "task : phonon\n"), "E003")


def test_e004_syntax(lint):
    assert "E004" in ids(lint(cell=GOOD_CELL + "%BLOCK species_mass\n Na 22.99\n"))
    assert "E004" in ids(lint(cell=GOOD_CELL + "%ENDBLOCK nothing\n"))


def test_e005_block_scalar_mismatch(lint):
    assert "E005" in ids(lint(cell=GOOD_CELL + "species_pot Na C19\n"))
    assert "E005" in ids(lint(cell=GOOD_CELL + "%BLOCK fix_all_cell\n true\n%ENDBLOCK fix_all_cell\n"))


def test_e011_positions_conflict(lint):
    cell = GOOD_CELL + "%BLOCK positions_abs\n Na 0 0 0\n%ENDBLOCK positions_abs\n"
    assert "E011" in ids(lint(cell=cell))


@pytest.mark.parametrize(
    "extra",
    ["kpoints_mp_grid 4 4 4\n", "%BLOCK kpoints_list\n 0 0 0 1\n%ENDBLOCK kpoints_list\n"],
)
def test_e012_kpoint_conflicts(lint, extra):
    (d,) = only(lint(cell=GOOD_CELL + extra), "E012")
    assert d.line > GOOD_CELL.count("\n")


def test_e013_unknown_element(lint):
    assert "E013" in ids(lint(cell=GOOD_CELL.replace("Na 0.0", "Xx 0.0")))
    assert "E013" not in ids(lint(cell=GOOD_CELL.replace("Na 0.0", "Na:1 0.0").replace("  Na C19", "  Na:1 C19")))
    assert "E013" not in ids(lint(cell=GOOD_CELL.replace("Na 0.0", "11 0.0").replace("  Na C19", "  11 C19")))


def test_e014_missing_structure(lint):
    assert ids(lint(cell="kpoints_mp_spacing 0.05\n", param=GOOD_PARAM)).count("E014") == 2


def test_e015_lattice_conflict(lint):
    cell = GOOD_CELL + "%BLOCK lattice_abc\n 5 5 5\n 90 90 90\n%ENDBLOCK lattice_abc\n"
    assert "E015" in ids(lint(cell=cell))


@pytest.mark.parametrize(
    "cell",
    [
        GOOD_CELL.replace("Cl 0.5 0.5 0.5", "Cl 0.5 0.5"),  # too few coordinates
        GOOD_CELL.replace("0.0 5.0 0.0", "0.0 5.0"),  # short lattice row
        GOOD_CELL.replace("0.0 0.0 5.0", "5.0 0.0 0.0").replace("0.0 5.0 0.0", "5.0 0.0 0.0"),  # zero volume
        GOOD_CELL.replace("Na C19", "Na C19 extra"),  # species_pot row
        GOOD_CELL + "%BLOCK kpoints_list\n 0 0\n%ENDBLOCK kpoints_list\n",
        GOOD_CELL.replace("0.5 0.5 0.5", "0.5 0.5 0.5 spin=high"),  # spin is not a number
    ],
)
def test_e016_malformed_blocks(lint, cell):
    assert "E016" in ids(lint(cell=cell))


def test_lattice_abc_and_units_are_understood(lint):
    cell = GOOD_CELL.replace(
        "%BLOCK lattice_cart\n  5.0 0.0 0.0\n  0.0 5.0 0.0\n  0.0 0.0 5.0\n%ENDBLOCK lattice_cart",
        "%BLOCK lattice_abc\n ang\n 5 5 5\n 90 90 90\n%ENDBLOCK lattice_abc",
    )
    assert lint(cell=cell) == []
    assert "E016" in ids(lint(cell=cell.replace("90 90 90", "0 0 0")))
    assert "E016" in ids(lint(cell=cell.replace(" ang\n", " furlongs\n")))


# ---------------------------------------------------------------- warnings


def test_w010_w011_species_pot_matching(lint):
    cell = GOOD_CELL.replace("  Cl C19\n", "")
    assert "W010" in ids(lint(cell=cell))
    assert "W011" in ids(lint(cell=GOOD_CELL.replace("  Cl C19\n", "  Cl C19\n  K C19\n")))
    assert "W010" not in ids(lint(cell=GOOD_CELL.replace("%BLOCK species_pot\n  Na C19\n  Cl C19\n%ENDBLOCK species_pot\n", "")))


def test_w020_irrelevant_task_keywords(lint):
    assert "W020" in ids(lint(param=GOOD_PARAM + "geom_method : lbfgs\n"))
    assert "W020" in ids(lint(param=GOOD_PARAM.replace("singlepoint", "geometryoptimization") + "md_ensemble : nvt\n"))
    assert "W020" not in ids(lint(param=GOOD_PARAM.replace("singlepoint", "geometryoptimization") + "geom_method : lbfgs\n"))
    assert "W020" in ids(lint(param=GOOD_PARAM.replace("task : singlepoint\n", "") + "geom_max_iter : 5\n"))  # default task


def test_w021_spin_without_polarisation(lint):
    spin_cell = GOOD_CELL.replace("Na 0.0 0.0 0.0", "Na 0.0 0.0 0.0 spin=2")
    for enabled in ("spin_polarised : true", "spin_polarized : true", "spin : 2"):
        assert "W021" not in ids(lint(cell=spin_cell, param=GOOD_PARAM + enabled + "\n"))
    assert "W021" in ids(lint(cell=spin_cell))
    assert "W021" not in ids(lint(cell=GOOD_CELL, param=GOOD_PARAM + "spin_polarised : true\n"))  # default spins are fine
    assert "W021" not in ids(lint(cell=GOOD_CELL.replace("Na 0.0 0.0 0.0", "Na 0.0 0.0 0.0 spin=0")))


def test_w022_bandstructure_without_bs_kpoints(lint):
    param = GOOD_PARAM.replace("singlepoint", "bandstructure")
    assert "W022" in ids(lint(param=param))
    assert "W022" not in ids(lint(cell=GOOD_CELL + "bs_kpoint_mp_spacing 0.05\n", param=param))


def test_w023_nothing_to_optimise(lint):
    param = GOOD_PARAM.replace("singlepoint", "geometryoptimization")
    assert "W023" in ids(lint(cell=GOOD_CELL + "fix_all_cell : true\nfix_all_ions : true\n", param=param))
    assert "W023" not in ids(lint(cell=GOOD_CELL + "fix_all_cell : true\n", param=param))


def test_w030_cutoff(lint):
    assert "W030" not in ids(lint(param="task : singlepoint\n"))  # CASTEP derives a cutoff from basis_precision
    assert "W030" not in ids(lint(param="basis_precision : fine\n"))
    assert "W030" in ids(lint(param="cut_off_energy : 100 eV\n"))
    assert "W030" in ids(lint(param="cut_off_energy : 3 Ha\n"))  # 81.6 eV
    assert "W030" not in ids(lint(param="cut_off_energy : 40 Ha\n"))  # 1088 eV
    assert "W030" not in ids(lint(cell=GOOD_CELL, param=None))  # no .param file at all


def test_w031_coarse_kpoints(lint):
    assert "W031" in ids(lint(cell=GOOD_CELL.replace("0.05", "0.3")))
    assert "W031" not in ids(lint(cell=GOOD_CELL.replace("0.05", "0.3 1/nm")))
    coarse = GOOD_CELL.replace("kpoints_mp_spacing 0.05", "kpoints_mp_grid 1 1 1")
    assert "W031" in ids(lint(cell=coarse))  # 5 A cell, gamma only: 0.2 1/A
    assert "W031" not in ids(lint(cell=coarse.replace("1 1 1", "4 4 4")))


def test_w032_smearing_with_fixed_occupancy(lint):
    assert "W032" in ids(lint(param=GOOD_PARAM + "fix_occupancy : true\nsmearing_width : 0.1 eV\n"))
    assert "W032" in ids(lint(param=GOOD_PARAM + "fix_occupancy : true\nsmearing_scheme : fermidirac\n"))
    assert "W032" not in ids(lint(param=GOOD_PARAM + "fix_occupancy : true\n"))
    assert "W032" not in ids(lint(param=GOOD_PARAM + "smearing_width : 0.1 eV\n"))
    # metals_method=dm with fixed occupancy is a legitimate choice of electronic minimiser
    assert "W032" not in ids(lint(param=GOOD_PARAM + "fix_occupancy : true\nmetals_method : dm\n"))


def test_e017_cutoff_and_precision(lint):
    assert "E017" in ids(lint(param=GOOD_PARAM + "basis_precision : fine\n"))


def test_w040_close_atoms_including_periodic_images(lint):
    close = GOOD_CELL.replace("Cl 0.5 0.5 0.5", "Cl 0.0 0.0 0.05")
    assert "W040" in ids(lint(cell=close))
    across_boundary = GOOD_CELL.replace("Cl 0.5 0.5 0.5", "Cl 0.99 0.0 0.0")  # 0.05 A through the boundary
    assert "W040" in ids(lint(cell=across_boundary))
    absolute = GOOD_CELL.replace("positions_frac", "positions_abs").replace("Cl 0.5 0.5 0.5", "Cl 0.1 0.0 0.0")
    assert "W040" in ids(lint(cell=absolute))
    assert "W040" not in ids(lint())


SKEWED_CELL = """\
%BLOCK lattice_cart
  10.0 0.0 0.0
  -7.341179 8.733959 0.0
  5.193406 -12.502975 2.133525
%ENDBLOCK lattice_cart
%BLOCK positions_frac
  Na 0.0 0.0 0.0
  Cl 0.01456 0.755587 0.249559
%ENDBLOCK positions_frac
kpoints_mp_spacing 0.05
"""


def test_w040_skewed_cell_uses_minimum_image(lint):
    # True minimum-image distance is 4.61 A. Searching +/-1 images of the *given* cell only
    # finds 5.11 A, so this would be missed without Minkowski reduction.
    near = Config(options={"min_distance": 4.8})
    assert "W040" in ids(lint(cell=SKEWED_CELL, config=near))
    assert "W040" not in ids(lint(cell=SKEWED_CELL))


def test_w041_snap_without_symmetry(lint):
    assert "W041" in ids(lint(cell=GOOD_CELL + "snap_to_symmetry : true\n"))
    assert "W041" not in ids(lint(cell=GOOD_CELL + "snap_to_symmetry : true\nsymmetry_generate\n"))


def test_i050_alias(lint):
    assert "I050" in ids(lint(cell=GOOD_CELL.replace("kpoints_mp_spacing", "kpoint_mp_spacing")))


# ------------------------------------------- behaviour checked against CASTEP 26.11


def test_keywords_ignore_underscores_and_case(lint):
    assert "E001" not in ids(lint(param=GOOD_PARAM.replace("cut_off_energy", "CutoffEnergy")))
    assert only(lint(param=GOOD_PARAM + "cutoffenergy : 5\n"), "E003")


def test_arithmetic_in_blocks(lint):
    cell = GOOD_CELL.replace("0.5 0.5 0.5", "1/4 0.5*0.5 cos(60)/2").replace("5.0 0.0 0.0", "0.5*10 0.0 0.0")
    assert lint(cell=cell) == []
    assert "E016" in ids(lint(cell=GOOD_CELL.replace("0.5 0.5 0.5", "1/ 0.5 0.5")))


def test_species_pot_library_line(lint):
    cell = GOOD_CELL.replace("  Na C19\n  Cl C19\n", "  C19\n")
    assert lint(cell=cell) == []


def test_task_spellings_accepted_by_castep(lint):
    for task in ("geometry optimization", "GeomOpt", "energy", "bandstructure"):
        assert "E002" not in ids(lint(param=GOOD_PARAM.replace("singlepoint", task)))


def test_w050_obsolete_keyword(lint):
    (d,) = only(lint(cell=GOOD_CELL + "bs_kpoint_mp_spacing 0.05\n"), "W050")
    assert "spectral_kpoint_mp_spacing" in d.message
