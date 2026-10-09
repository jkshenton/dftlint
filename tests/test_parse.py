from dftlint.castep.parse import parse_text


def test_scalars_blocks_and_positions():
    text = "%BLOCK lattice_cart\n 1 0 0\n%ENDBLOCK lattice_cart\nkpoints_mp_spacing : 0.05 1/ang  ! comment\nsymmetry_generate\n"
    pf = parse_text(text, "x.cell", "cell")
    assert not pf.syntax_errors
    assert [b.key for b in pf.blocks] == ["lattice_cart"]
    assert pf.blocks[0].rows[0].line == 2
    spacing, bare = pf.entries
    assert (spacing.key, spacing.value, spacing.line, spacing.col) == ("kpoints_mp_spacing", "0.05 1/ang", 4, 1)
    assert text.splitlines()[3][spacing.vcol - 1 : spacing.vend - 1] == "0.05 1/ang"
    assert (bare.key, bare.value) == ("symmetry_generate", "")  # bare keywords are kept


def test_comment_styles_and_case():
    pf = parse_text("# a\n! b\n; c\nTASK = SinglePoint # trailing\n", "x.param", "param")
    assert [(e.key, e.value) for e in pf.entries] == [("task", "SinglePoint")]


def test_unterminated_and_nested_blocks():
    text = "%BLOCK a\n 1\n%BLOCK b\n 2\n"
    pf = parse_text(text, "x.cell", "cell")
    messages = [e.message for e in pf.syntax_errors]
    assert len(messages) == 2
    assert "not closed" in messages[0] and "never closed" in messages[1]
    assert [e.line for e in pf.syntax_errors] == [1, 3]


def test_mismatched_and_stray_endblock():
    pf = parse_text("%BLOCK a\n%ENDBLOCK b\n%ENDBLOCK c\n", "x.cell", "cell")
    assert [e.line for e in pf.syntax_errors] == [2, 3]


def test_unparsable_line():
    pf = parse_text("= 5\n", "x.cell", "cell")
    assert [e.line for e in pf.syntax_errors] == [1]


def test_suppression_directives():
    text = "a 1  # dftlint: disable=E001\n# dftlint: disable=W,I050\nb 2\nc 3\n# dftlint: disable-file=E003\n"
    pf = parse_text(text, "x.cell", "cell")
    assert pf.is_suppressed("E001", 1) and not pf.is_suppressed("E002", 1)
    assert pf.is_suppressed("W030", 3) and pf.is_suppressed("I050", 3)  # standalone applies to next code line
    assert not pf.is_suppressed("W030", 4)
    assert pf.is_suppressed("E003", 4)  # file-wide
