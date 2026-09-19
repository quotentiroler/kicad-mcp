"""Tests for the S-expression reader."""

from pathlib import Path

import pytest

from kicad_mcp.utils.sexpr import form_end, parse_sexpr, sexpr_to_string, split_forms

BACKSLASH = chr(92)


def test_quoted_parens_do_not_change_depth():
    """KiCad writes parens inside quoted strings as a matter of course.

    Net names for unconnected pads look like "unconnected-(U1-Pad3)", and
    descriptions carry whole sentences. A reader that counts brackets in
    the raw text loses the rest of the file the moment one of them is
    unmatched.
    """
    parsed = parse_sexpr('(a (descr "smile :-)") (b 1))')
    assert parsed == ["a", ["descr", '"smile :-)"'], ["b", "1"]]


def test_string_ending_in_escaped_backslash_closes():
    r"""KiCad writes 3D model paths as (model "C:\lib\foo.wrl").

    Escaped as \\, so the character before the closing quote is a
    backslash. Treating that quote as escaped runs the string to the end
    of the file and swallows everything after it.
    """
    text = '(model "C:' + BACKSLASH * 2 + '") (layer "F.Cu")'
    forms = split_forms(text)
    assert len(forms) == 2
    assert forms[1] == ["layer", '"F.Cu"']


def test_escaped_quote_inside_string_does_not_close():
    text = '(descr "say ' + BACKSLASH + '"hi' + BACKSLASH + '" now") (b 1)'
    forms = split_forms(text)
    assert len(forms) == 2
    assert forms[0][0] == "descr"


def test_split_forms_keeps_every_top_level_form():
    assert split_forms("(a 1) (b 2) (c 3)") == [["a", "1"], ["b", "2"], ["c", "3"]]


def test_parse_sexpr_always_returns_a_list():
    assert parse_sexpr("") == []
    assert parse_sexpr("bare token") == []


def test_unbalanced_input_is_rejected():
    with pytest.raises(ValueError):
        parse_sexpr('(footprint "R" (at 1 2)')


def test_stray_closing_paren_is_rejected():
    with pytest.raises(ValueError):
        parse_sexpr("(a 1))")


def test_token_equal_to_head_is_not_hoisted_onto_the_head_line():
    """sexpr_to_string used list.index, which finds the first equal value.

    A token that happens to equal element 0 was appended to the opening
    line instead of its own, so (net (a b) net) rendered as (netnet ...).
    """
    out = sexpr_to_string(["net", ["a", "b"], "net"])
    assert "netnet" not in out
    assert parse_sexpr(out) == ["net", ["a", "b"], "net"]


def test_form_end_reports_a_form_that_never_closes():
    assert form_end('(footprint "R" (at 1 2))', 0) == 24
    assert form_end('(footprint "R" (at 1 2)', 0) is None


def test_no_module_hand_rolls_its_own_bracket_scanner():
    """Counting brackets without knowing where the strings are loses data.

    Eight copies of that loop across four modules each dropped whatever
    followed an unmatched paren in a quoted value. form_span is the one
    that knows about strings and escapes, so nothing should grow a
    private replacement for it.
    """
    package = Path(__file__).resolve().parents[3] / "kicad_mcp"
    offenders = [
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if path.name != "sexpr.py" and 'char == "("' in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


@pytest.mark.parametrize(
    "sexpr",
    [
        ["at", "1", "2"],
        ["footprint", '"R_0402"', ["layer", '"F.Cu"'], ["at", "1", "2"], ["descr", '"a (b) c"']],
        ["layers", '"F.Cu"', '"F.Paste"', '"F.Mask"', '"B.Cu"'],
        ["net", "1", '"unconnected-(U1-Pad3)"'],
    ],
)
def test_round_trip(sexpr):
    assert parse_sexpr(sexpr_to_string(sexpr)) == sexpr
