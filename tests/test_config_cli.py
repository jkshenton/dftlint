from __future__ import annotations

import json

import pytest

from conftest import GOOD_CELL, GOOD_PARAM, ids
from dftlint.cli import main
from dftlint.core import Config, config_from_dict, load_config

BAD_PARAM = GOOD_PARAM + "cut_of_energy : 5\nspin_polarised : maybe\n"


def test_suppression_inline_standalone_and_file(lint):
    base = GOOD_PARAM
    assert "E001" in ids(lint(param=base + "cut_of_energy : 5\n"))
    assert "E001" not in ids(lint(param=base + "cut_of_energy : 5  # dftlint: disable=E001\n"))
    assert "E001" not in ids(lint(param=base + "# dftlint: disable=E001\ncut_of_energy : 5\n"))
    assert "E001" not in ids(lint(param=base + "cut_of_energy : 5\n# dftlint: disable-file=E001\n"))
    assert "E002" in ids(lint(param=base + "spin_polarised : maybe  # dftlint: disable=E001\n"))  # other rules still fire


def test_select_ignore_prefixes(lint):
    cell = GOOD_CELL + "kpoint_mp_grid 4 4 4\n"
    assert {"E012", "I050"} <= set(ids(lint(cell=cell)))
    assert "I050" not in ids(lint(cell=cell, config=Config(ignore=("I",))))
    assert set(ids(lint(cell=cell, config=Config(select=("E012",))))) == {"E012"}


def test_options_change_thresholds(lint):
    assert "W030" not in ids(lint(param="cut_off_energy : 300 eV\n"))
    assert "W030" in ids(lint(param="cut_off_energy : 300 eV\n", config=Config(options={"min_cutoff_ev": 400})))


def test_config_file_discovery_and_validation(tmp_path):
    (tmp_path / ".dftlint.toml").write_text('ignore = ["w"]\n')
    sub = tmp_path / "calc"
    sub.mkdir()
    config = load_config(sub)
    assert config.ignore == ("W",)
    with pytest.raises(ValueError):
        config_from_dict({"nonsense": 1})


def test_cli_exit_codes_and_text(tmp_path, capsys):
    (tmp_path / "ok.cell").write_text(GOOD_CELL)
    (tmp_path / "ok.param").write_text(GOOD_PARAM)
    assert main([str(tmp_path / "ok.cell")]) == 0
    assert "All checks passed (2 files)" in capsys.readouterr().out

    (tmp_path / "bad.param").write_text(BAD_PARAM)
    assert main([str(tmp_path / "bad.param"), "--no-color"]) == 1
    out = capsys.readouterr().out
    assert "bad.param:" in out and "E001" in out and "E002" in out

    (tmp_path / "warn.param").write_text("cut_off_energy : 100 eV\n")  # only W030
    assert main([str(tmp_path / "warn.param")]) == 0
    assert main([str(tmp_path / "warn.param"), "--strict"]) == 1
    capsys.readouterr()

    assert main([str(tmp_path / "missing.cell")]) == 2
    assert main([]) == 2


def test_cli_json(tmp_path, capsys):
    (tmp_path / "bad.param").write_text(BAD_PARAM)
    main([str(tmp_path / "bad.param"), "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    first = data["diagnostics"][0]
    assert {"rule", "severity", "message", "file", "line", "col", "end_col"} <= set(first)
    assert data["files_checked"] == 1


def test_cli_directory_recursion_and_sibling_pairing(tmp_path, capsys):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "x.cell").write_text(GOOD_CELL + "spin_polarised\n")
    (nested / "x.param").write_text(GOOD_PARAM)
    main([str(tmp_path), "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data["files_checked"] == 2
    # passing only the .cell still pulls in x.param, enabling cross-file rules
    cell = nested / "x.cell"
    cell.write_text(GOOD_CELL.replace("Na 0.0 0.0 0.0", "Na 0.0 0.0 0.0 spin=2"))
    main([str(cell), "--format", "json"])
    assert {d["rule"] for d in json.loads(capsys.readouterr().out)["diagnostics"]} == {"W021"}


def test_cli_list_and_explain(capsys):
    assert main(["--list-rules"]) == 0
    listing = capsys.readouterr().out
    assert "E001" in listing and "W040" in listing
    assert main(["--explain", "w031"]) == 0
    assert "max_kspacing" in capsys.readouterr().out
    assert main(["--explain", "Z999"]) == 2


def test_cli_select_and_ignore_flags(tmp_path, capsys):
    (tmp_path / "bad.param").write_text(BAD_PARAM)
    main([str(tmp_path / "bad.param"), "--format", "json", "--select", "E002"])
    assert {d["rule"] for d in json.loads(capsys.readouterr().out)["diagnostics"]} == {"E002"}
    main([str(tmp_path / "bad.param"), "--format", "json", "--ignore", "E,W"])
    assert json.loads(capsys.readouterr().out)["diagnostics"] == []


def test_cross_check_with_castep_outputs(tmp_path, capsys):
    pytest.importorskip("castep_outputs")
    (tmp_path / "bad.cell").write_text("%BLOCK lattice_cart\n 5 0 0\n")  # never closed
    main([str(tmp_path / "bad.cell"), "--format", "json", "--cross-check"])
    found = {d["rule"] for d in json.loads(capsys.readouterr().out)["diagnostics"]}
    assert {"E004", "W060"} <= found


def test_internal_rule_errors_are_reported_not_fatal(tmp_path, monkeypatch, lint):
    from dftlint.core import REGISTRY

    def boom(ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(REGISTRY, "E017", REGISTRY["E017"].__class__(**{**REGISTRY["E017"].__dict__, "func": boom}))
    diags = lint()
    assert ids(diags) == ["E999"] and "kaboom" in diags[0].message
