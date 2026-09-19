"""Tests for parsing .kicad_sch files."""

import contextlib
import io

import pytest

from kicad_mcp.utils.netlist_parser import SchematicParser, extract_netlist

SCH = """(kicad_sch
\t(version 20241229)
\t(lib_symbols
\t\t(symbol "Device:R"
\t\t\t(property "Reference" "R")
\t\t\t(property "Description" "Resistor (generic)")
\t\t)
\t)
\t(symbol
\t\t(lib_id "Device:R")
\t\t(at 100 100 0)
\t\t(uuid "aaaa")
\t\t(property "Reference" "R1")
\t\t(property "Value" "{value}")
\t\t(property "Footprint" "Resistor_SMD:R_0402_1005Metric")
\t)
\t(symbol
\t\t(lib_id "Device:C")
\t\t(at 120 100 0)
\t\t(uuid "bbbb")
\t\t(property "Reference" "C1")
\t\t(property "Value" "100nF")
\t\t(property "Footprint" "Capacitor_SMD:C_0402_1005Metric")
\t)
)
"""


def parse(tmp_path, value="4.7k"):
    path = tmp_path / "sheet.kicad_sch"
    path.write_text(SCH.format(value=value), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        return SchematicParser(str(path)).parse()


def test_parses_symbol_instances(tmp_path):
    out = parse(tmp_path)

    assert sorted(out["components"]) == ["C1", "R1"]
    assert out["components"]["R1"]["lib_id"] == "Device:R"
    assert out["components"]["R1"]["value"] == "4.7k"
    assert out["components"]["C1"]["footprint"] == "Capacitor_SMD:C_0402_1005Metric"


def test_library_symbol_templates_are_not_components(tmp_path):
    """lib_symbols carries (property "Reference" "R") as a prefix template."""
    out = parse(tmp_path)

    assert "R" not in out["components"]


@pytest.mark.parametrize("value", ["4.7k (1%", "4.7k :-)", "100nF ((", "1uF ))"])
def test_a_paren_in_a_value_does_not_drop_the_component(tmp_path, value):
    """Someone can type anything into a Value field.

    Counting brackets in the raw text treats those as structure, so the
    symbol's extent runs to the wrong place and it drops out of the
    netlist. Nothing errors, the component is simply not there.
    """
    out = parse(tmp_path, value=value)

    assert sorted(out["components"]) == ["C1", "R1"]


def test_missing_file_is_reported_not_raised(tmp_path):
    out = extract_netlist(str(tmp_path / "nope.kicad_sch"))

    assert out["error"]
    assert out["components"] == {}


def test_empty_file_yields_no_components(tmp_path):
    path = tmp_path / "empty.kicad_sch"
    path.write_text("", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        out = extract_netlist(str(path))

    assert out["component_count"] == 0
