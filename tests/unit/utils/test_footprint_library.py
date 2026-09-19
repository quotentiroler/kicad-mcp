"""Tests for building a footprint from a KiCad library definition."""

from unittest import mock

from kicad_mcp.utils import footprint_library
from kicad_mcp.utils.footprint_library import create_full_footprint_sexpr

PADS_ONLY = """(footprint "Resistor_SMD:R_0402_1005Metric"
\t(descr "Resistor SMD 0402 (1005 Metric)")
\t(pad "1" smd roundrect
\t\t(at -0.48 0)
\t\t(size 0.56 0.62)
\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t)
)
"""

WITH_LAYER = """(footprint "Resistor_SMD:R_0402_1005Metric"
\t(layer "F.Cu")
\t(descr "Resistor SMD 0402 (1005 Metric)")
\t(pad "1" smd roundrect
\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t)
)
"""


def build(lib_content, layer="B.Cu"):
    with mock.patch.object(
        footprint_library, "read_footprint_from_library", return_value=lib_content
    ):
        return create_full_footprint_sexpr(
            "R1", "Resistor_SMD:R_0402_1005Metric", (10.0, 20.0), layer=layer
        )


def test_layer_is_applied_when_the_library_only_has_pad_layers():
    """A pad's (layers ...) is not the footprint's (layer ...).

    Testing for the substring "(layer" matches "(layers" too, so a
    footprint that only declares pad layers took the update branch, the
    update pattern found nothing, and the part silently stayed on the
    front of the board.
    """
    out = build(PADS_ONLY, layer="B.Cu")

    assert '(layer "B.Cu")' in out
    assert '(layers "F.Cu" "F.Paste" "F.Mask")' in out


def test_existing_footprint_layer_is_replaced_not_duplicated():
    out = build(WITH_LAYER, layer="B.Cu")

    assert '(layer "B.Cu")' in out
    assert '(layer "F.Cu")' not in out
    assert out.count('(layer "B.Cu")') == 1


def test_pad_layers_are_never_rewritten():
    out = build(WITH_LAYER, layer="B.Cu")

    assert '(layers "F.Cu" "F.Paste" "F.Mask")' in out


def test_missing_library_falls_back_to_a_minimal_footprint():
    out = build(None, layer="B.Cu")

    assert '(layer "B.Cu")' in out
    assert "R1" in out
