from __future__ import annotations

from pathlib import Path

import pytest

from dftlint.core import Config
from dftlint.runner import lint_paths

GOOD_CELL = """\
%BLOCK lattice_cart
  5.0 0.0 0.0
  0.0 5.0 0.0
  0.0 0.0 5.0
%ENDBLOCK lattice_cart
%BLOCK positions_frac
  Na 0.0 0.0 0.0
  Cl 0.5 0.5 0.5
%ENDBLOCK positions_frac
%BLOCK species_pot
  Na C19
  Cl C19
%ENDBLOCK species_pot
kpoints_mp_spacing 0.05
"""

GOOD_PARAM = """\
task : singlepoint
xc_functional : PBE
cut_off_energy : 500 eV
"""


@pytest.fixture
def lint(tmp_path: Path):
    """lint(cell=..., param=..., config=...) -> list of diagnostics. Defaults are a clean pair."""

    def run(cell: str | None = GOOD_CELL, param: str | None = GOOD_PARAM, config: Config | None = None):
        paths = []
        if cell is not None:
            (tmp_path / "seed.cell").write_text(cell)
            paths.append(tmp_path / "seed.cell")
        if param is not None:
            (tmp_path / "seed.param").write_text(param)
            paths.append(tmp_path / "seed.param")
        return lint_paths(paths, config or Config()).diagnostics

    return run


def ids(diagnostics) -> list[str]:
    return [d.rule_id for d in diagnostics]
