"""Tests for reading what kicad-cli actually put in a SPICE netlist.

Both decks here came out of kicad-cli 9.0: LINEAR from a two-pole RC
sheet, REAL from a routed board with an nRF52840 on it.
"""

import pytest

from kicad_mcp.tools.export_tools import summarise_spice_netlist

LINEAR = """.title KiCad schematic
R1 /in /a 1k
C1 /a 0 10n
R2 /a /out 10k
V1 /in 0 DC 1
C2 /out 0 100n
.end
"""

REAL = """.title KiCad schematic
C2 V1V8 unconnected-_C2-Pad2_ 4.7u
U2 __U2
C4 V1V8 unconnected-_C4-Pad2_ 4.7u
R7 VCC /STAT 1k
Y2 __Y2
ANT2 __ANT2
C20 VCC GND 4.7u
L2 unconnected-_L2-Pad1_ /DCC 10u
.end
"""


def test_counts_devices_by_kind():
    out = summarise_spice_netlist(LINEAR)

    assert out["devices"] == {"R": 2, "C": 2, "V": 1}


def test_a_linear_driven_grounded_deck_is_simulatable():
    out = summarise_spice_netlist(LINEAR)

    assert out["simulatable"] is True
    assert out["blockers"] == []
    assert out["ground_node"] == "0"
    assert out["unmodelled"] == []


def test_parts_with_no_model_are_named():
    out = summarise_spice_netlist(REAL)

    assert out["unmodelled"] == ["ANT2", "U2", "Y2"]


def test_a_board_full_of_silicon_is_not_simulatable():
    out = summarise_spice_netlist(REAL)

    assert out["simulatable"] is False
    assert out["devices"] == {"C": 3, "R": 1, "L": 1}


@pytest.mark.parametrize(
    "fragment",
    ["no voltage source", "GND", "no model"],
)
def test_every_blocker_is_explained(fragment):
    """A caller that cannot simulate should be told which thing stopped it."""
    blockers = " ".join(summarise_spice_netlist(REAL)["blockers"])

    assert fragment in blockers


def test_unconnected_nets_are_counted():
    """KiCad names a dangling pad's net unconnected-<ref>-Pad<n>."""
    out = summarise_spice_netlist(REAL)

    assert out["unconnected_nets"] == 3


def test_ground_named_gnd_is_reported_as_such():
    """SPICE takes node 0 as the reference, and KiCad does not write it."""
    out = summarise_spice_netlist(REAL)

    assert out["ground_node"] == "GND"


def test_an_empty_deck_is_not_simulatable():
    out = summarise_spice_netlist("")

    assert out["simulatable"] is False
    assert out["devices"] == {}


def test_comments_and_directives_are_not_devices():
    out = summarise_spice_netlist("* a comment\n.title x\n.end\n")

    assert out["devices"] == {}
    assert out["unmodelled"] == []
