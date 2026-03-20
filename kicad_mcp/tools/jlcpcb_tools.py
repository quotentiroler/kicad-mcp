"""
JLCPCB BOM and CPL export tools for KiCad projects.

Generates assembly-ready files in JLCPCB's required format:
- BOM CSV: Comment, Designator, Footprint, LCSC Part #
- CPL CSV: Designator, Mid X, Mid Y, Rotation, Layer
"""
import csv
import os
import re
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files


# JLCPCB BOM columns
JLCPCB_BOM_HEADER = ["Comment", "Designator", "Footprint", "LCSC Part #"]

# JLCPCB CPL columns
JLCPCB_CPL_HEADER = ["Designator", "Mid X", "Mid Y", "Rotation", "Layer"]


def register_jlcpcb_tools(mcp: FastMCP) -> None:
    """Register JLCPCB export tools with the MCP server."""

    @mcp.tool()
    def export_jlcpcb_bom(
        project_path: str,
        output_dir: str = "",
    ) -> Dict[str, Any]:
        """Export BOM and CPL files in JLCPCB assembly format.

        Generates two CSV files ready for JLCPCB SMT assembly ordering:
        - {project}_BOM.csv — Bill of Materials with LCSC part numbers
        - {project}_CPL.csv — Component Placement List with positions

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            output_dir: Output directory (default: project_dir/jlcpcb)

        Returns:
            Dictionary with export results and file paths
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}

        pcb_path = files["pcb"]
        project_dir = os.path.dirname(pcb_path)
        project_name = os.path.splitext(os.path.basename(pcb_path))[0]

        if not output_dir:
            output_dir = os.path.join(project_dir, "jlcpcb")
        os.makedirs(output_dir, exist_ok=True)

        # Parse PCB for component data
        components = _parse_pcb_components(pcb_path)
        if not components:
            return {"error": "No components found in PCB file"}

        # Group by value+footprint for BOM
        bom_groups = _group_for_bom(components)

        # Write BOM CSV
        bom_path = os.path.join(output_dir, f"{project_name}_BOM.csv")
        bom_rows = 0
        with open(bom_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(JLCPCB_BOM_HEADER)
            for group in bom_groups:
                writer.writerow([
                    group["comment"],
                    group["designators"],
                    group["footprint"],
                    group["lcsc"],
                ])
                bom_rows += 1

        # Write CPL CSV
        cpl_path = os.path.join(output_dir, f"{project_name}_CPL.csv")
        cpl_rows = 0
        with open(cpl_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(JLCPCB_CPL_HEADER)
            for comp in components:
                layer = "Top" if comp["layer"] == "F.Cu" else "Bottom"
                writer.writerow([
                    comp["reference"],
                    f"{comp['x']:.4f}mm",
                    f"{comp['y']:.4f}mm",
                    f"{comp['rotation']:.1f}",
                    layer,
                ])
                cpl_rows += 1

        return {
            "success": True,
            "bom_path": bom_path,
            "cpl_path": cpl_path,
            "bom_rows": bom_rows,
            "cpl_rows": cpl_rows,
            "component_count": len(components),
            "unique_parts": len(bom_groups),
        }


def _parse_pcb_components(pcb_path: str) -> List[Dict[str, Any]]:
    """Parse footprint data from a .kicad_pcb file.

    Extracts reference, value, footprint, position, rotation, layer,
    and any LCSC property from each footprint block.
    """
    with open(pcb_path, "r", encoding="utf-8") as f:
        content = f.read()

    components = []
    # Find footprint blocks — KiCad 8/9 format
    fp_pattern = re.compile(r'\(footprint\s+"([^"]*)"', re.DOTALL)
    pos = 0

    while True:
        match = fp_pattern.search(content, pos)
        if not match:
            break

        fp_start = match.start()
        footprint_lib = match.group(1)

        # Find the balanced closing paren
        depth = 0
        fp_end = fp_start
        for i in range(fp_start, len(content)):
            if content[i] == "(":
                depth += 1
            elif content[i] == ")":
                depth -= 1
                if depth == 0:
                    fp_end = i + 1
                    break

        block = content[fp_start:fp_end]
        pos = fp_end

        # Extract fields
        ref = _extract_property(block, "Reference") or _extract_fp_field(block, "reference")
        value = _extract_property(block, "Value") or _extract_fp_field(block, "value")
        footprint_name = _extract_property(block, "Footprint") or footprint_lib
        lcsc = _extract_property(block, "LCSC") or _extract_property(block, "LCSC Part #") or ""

        # Extract position: (at X Y rotation?)
        at_match = re.search(r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\)', block)
        if not at_match:
            continue

        x = float(at_match.group(1))
        y = float(at_match.group(2))
        rotation = float(at_match.group(3)) if at_match.group(3) else 0.0

        # Determine layer
        layer_match = re.search(r'\(layer\s+"([^"]+)"\)', block)
        layer = layer_match.group(1) if layer_match else "F.Cu"

        # Skip board-level items without a reference
        if not ref or ref.startswith("#") or ref == "REF**":
            continue

        components.append({
            "reference": ref,
            "value": value or "",
            "footprint": footprint_name,
            "lcsc": lcsc,
            "x": x,
            "y": y,
            "rotation": rotation,
            "layer": layer,
        })

    return components


def _extract_property(block: str, prop_name: str) -> str:
    """Extract a named property value from a footprint block.

    Handles KiCad 8/9 (property ...) syntax.
    """
    pattern = re.compile(
        rf'\(property\s+"{re.escape(prop_name)}"\s+"([^"]*)"\s*',
        re.IGNORECASE,
    )
    m = pattern.search(block)
    return m.group(1) if m else ""


def _extract_fp_field(block: str, field_name: str) -> str:
    """Extract fp_text reference/value from older KiCad format."""
    pattern = re.compile(
        rf'\(fp_text\s+{re.escape(field_name)}\s+"?([^"\n)]+)"?\s*\(',
        re.IGNORECASE,
    )
    m = pattern.search(block)
    return m.group(1).strip() if m else ""


def _group_for_bom(components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group components by value + footprint for BOM consolidation."""
    groups: Dict[str, Dict[str, Any]] = {}

    for comp in components:
        key = f"{comp['value']}||{comp['footprint']}||{comp['lcsc']}"
        if key not in groups:
            groups[key] = {
                "comment": comp["value"],
                "footprint": comp["footprint"],
                "lcsc": comp["lcsc"],
                "refs": [],
            }
        groups[key]["refs"].append(comp["reference"])

    result = []
    for group in groups.values():
        # Sort references naturally (C1, C2, C10 not C1, C10, C2)
        group["refs"].sort(key=_natural_sort_key)
        result.append({
            "comment": group["comment"],
            "designators": ", ".join(group["refs"]),
            "footprint": group["footprint"],
            "lcsc": group["lcsc"],
        })

    return result


def _natural_sort_key(s: str):
    """Sort key for natural ordering of component references."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", s)
    ]
