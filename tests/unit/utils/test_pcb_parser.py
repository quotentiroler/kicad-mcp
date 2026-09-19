"""Tests for parsing .kicad_pcb files.

The fixture mirrors the syntax KiCad 9 actually writes, checked against a
real routed board: tab-indented multi-line forms, quoted layer names, and
descriptions carrying parens.
"""

import pytest

from kicad_mcp.utils.pcb_parser import parse_pcb_file

BOARD = """(kicad_pcb
\t(version 20241229)
\t(generator "pcbnew")
\t(net 0 "")
\t(net 1 "unconnected-(ANT2-A-Pad1)")
\t(net 2 "V1V8")
\t(gr_rect
\t\t(start 170 -60)
\t\t(end 205 -35)
\t\t(layer "Edge.Cuts")
\t)
\t(footprint "Capacitor_SMD:C_0402_1005Metric"
\t\t(layer "F.Cu")
\t\t(uuid "0dee4990-1740-4a58-a727-d2fc26a5e22d")
\t\t(at 172 -46)
\t\t(descr "Capacitor SMD 0402 (1005 Metric), square (rectangular) end terminal")
\t\t(property "Reference" "C16")
\t)
\t(footprint "Resistor_SMD:R_0402_1005Metric"
\t\t(layer "B.Cu")
\t\t(uuid "1dee4990-1740-4a58-a727-d2fc26a5e22e")
\t\t(at 180 -48 90)
\t\t(property "Reference" "R6")
\t)
\t(segment
\t\t(start 185.1781 -40)
\t\t(end 180.1777 -45.0004)
\t\t(width 0.127)
\t\t(layer "F.Cu")
\t\t(net 2)
\t\t(uuid "1adcfe65-7de7-41bc-a5ca-5dc80f3025f1")
\t)
\t(via
\t\t(at 173.931 -56.3247)
\t\t(size 0.45)
\t\t(drill 0.25)
\t\t(layers "F.Cu" "B.Cu")
\t\t(net 2)
\t\t(uuid "4ef485b4-474d-49a7-8057-45a83f82b5ec")
\t)
)
"""


def write(tmp_path, text, name="board.kicad_pcb"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_parses_a_kicad_9_board(tmp_path):
    pcb = parse_pcb_file(write(tmp_path, BOARD))

    assert pcb.version == "20241229"
    assert [f.reference for f in pcb.footprints] == ["C16", "R6"]
    assert pcb.nets == {0: "", 1: "unconnected-(ANT2-A-Pad1)", 2: "V1V8"}
    assert len(pcb.tracks) == 1
    assert len(pcb.vias) == 1
    assert pcb.board_outline == [(170.0, -60.0), (205.0, -60.0), (205.0, -35.0), (170.0, -35.0)]


def test_footprint_fields_are_read_from_the_right_place(tmp_path):
    pcb = parse_pcb_file(write(tmp_path, BOARD))
    c16, r6 = pcb.footprints

    assert c16.footprint_lib == "Capacitor_SMD:C_0402_1005Metric"
    assert c16.position == (172.0, -46.0)
    assert c16.layer == "F.Cu"
    assert r6.position == (180.0, -48.0)
    assert r6.rotation == 90.0
    assert r6.layer == "B.Cu"


def test_unbalanced_paren_in_a_property_does_not_drop_the_component(tmp_path):
    """A component must not disappear because of what someone typed.

    Bracket-counting over the raw text treats a paren inside a quoted
    value as structure, runs the footprint's extent to the wrong place
    and drops it. Nothing errors: the component is simply absent from
    every DRC, BOM and placement answer that follows.
    """
    board = BOARD.replace(
        '(property "Reference" "C16")',
        '(property "Value" "4.7k :-)")\n\t\t(property "Reference" "C16")',
    )
    pcb = parse_pcb_file(write(tmp_path, board))

    assert [f.reference for f in pcb.footprints] == ["C16", "R6"]


def test_truncated_file_is_reported(tmp_path):
    truncated = BOARD[: BOARD.index("(segment")]
    with pytest.raises(ValueError):
        parse_pcb_file(write(tmp_path, truncated))


def test_malformed_coordinates_do_not_crash(tmp_path):
    """(at . .) is not a number, and a stray keystroke should not be fatal."""
    board = BOARD.replace("(at 172 -46)", "(at . .)")
    pcb = parse_pcb_file(write(tmp_path, board))

    assert [f.reference for f in pcb.footprints] == ["C16", "R6"]
    assert pcb.footprints[0].position == (0.0, 0.0)


def test_empty_file_is_reported(tmp_path):
    with pytest.raises(ValueError):
        parse_pcb_file(write(tmp_path, ""))


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_pcb_file(str(tmp_path / "nope.kicad_pcb"))
