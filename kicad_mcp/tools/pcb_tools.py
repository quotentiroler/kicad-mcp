"""
PCB layout tools for KiCad MCP Server.

Provides tools for component placement, trace routing, and PCB management.
"""

import os
import logging
import subprocess
from typing import Any
from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.pcb_parser import (
    parse_pcb_file,
    update_footprint_position,
    add_track_to_pcb,
    add_via_to_pcb,
    get_net_id_by_name,
    get_board_bounds,
    import_footprints_from_schematic,
    Track,
    Via,
    PCBData,
)
from kicad_mcp.utils.netlist_parser import extract_netlist
from kicad_mcp.utils.kicad_cli import get_kicad_cli_path, is_kicad_cli_available

logger = logging.getLogger(__name__)


def _register_pcb_layout_tools(mcp: FastMCP) -> None:
    """Register PCB layout tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """
    
    @mcp.tool()
    def get_pcb_info(project_path: str) -> dict[str, Any]:
        """Get information about the PCB layout including components and their positions.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with PCB info including footprints, tracks, vias, and board size
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        try:
            pcb = parse_pcb_file(files["pcb"])
            bounds = get_board_bounds(pcb)
            
            return {
                "pcb_file": files["pcb"],
                "version": pcb.version,
                "board_size": {
                    "width": bounds[2] - bounds[0],
                    "height": bounds[3] - bounds[1],
                    "origin": (bounds[0], bounds[1])
                },
                "nets": {str(k): v for k, v in pcb.nets.items()},
                "footprint_count": len(pcb.footprints),
                "track_count": len(pcb.tracks),
                "via_count": len(pcb.vias),
                "footprints": [
                    {
                        "reference": fp.reference,
                        "footprint": fp.footprint_lib,
                        "position": fp.position,
                        "rotation": fp.rotation,
                        "layer": fp.layer
                    }
                    for fp in pcb.footprints
                ]
            }
        except Exception as e:
            logger.error(f"Error parsing PCB: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def place_component(
        project_path: str,
        reference: str,
        x: float,
        y: float,
        rotation: float = 0.0
    ) -> dict[str, Any]:
        """Place or move a component on the PCB.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            reference: Component reference designator (e.g., "U1", "C1", "R1")
            x: X position in mm from board origin (0,0 = top-left corner of board)
            y: Y position in mm from board origin (0,0 = top-left corner of board)
            rotation: Rotation in degrees (0, 90, 180, 270)
            
        Returns:
            Result of the placement operation
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        
        try:
            # Get board bounds to convert relative coords to absolute
            pcb = parse_pcb_file(pcb_path)
            bounds = get_board_bounds(pcb)
            board_origin_x = bounds[0]  # min_x of board outline
            board_origin_y = bounds[1]  # min_y of board outline
            
            # Convert user coordinates (relative to board) to absolute KiCad coordinates
            abs_x = x + board_origin_x
            abs_y = y + board_origin_y
            
            success = update_footprint_position(
                pcb_path=pcb_path,
                reference=reference,
                new_position=(abs_x, abs_y),
                new_rotation=rotation
            )
            
            if success:
                return {
                    "success": True,
                    "message": f"Placed {reference} at board position ({x}, {y}) [absolute: ({abs_x}, {abs_y})] with {rotation}° rotation",
                    "reference": reference,
                    "position": (x, y),
                    "absolute_position": (abs_x, abs_y),
                    "board_origin": (board_origin_x, board_origin_y),
                    "rotation": rotation
                }
            else:
                return {
                    "success": False,
                    "error": f"Component {reference} not found in PCB. Run 'Update PCB from Schematic' in KiCad first."
                }
        except Exception as e:
            logger.error(f"Error placing component: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def place_multiple_components(
        project_path: str,
        placements: list[dict]
    ) -> dict[str, Any]:
        """Place multiple components on the PCB at once.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            placements: List of placement dictionaries, each with:
                - reference: Component reference (e.g., "U1")
                - x: X position in mm from board origin (0,0 = top-left corner)
                - y: Y position in mm from board origin (0,0 = top-left corner)
                - rotation: Rotation in degrees (optional, default 0)
                
        Returns:
            Summary of placement results
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        
        # Get board bounds to convert relative coords to absolute
        try:
            pcb = parse_pcb_file(pcb_path)
            bounds = get_board_bounds(pcb)
            board_origin_x = bounds[0]  # min_x of board outline
            board_origin_y = bounds[1]  # min_y of board outline
        except Exception as e:
            return {"error": f"Failed to parse PCB for board bounds: {e}"}
        
        results = {"success": [], "failed": [], "board_origin": (board_origin_x, board_origin_y)}
        
        for placement in placements:
            ref = placement.get("reference")
            x = placement.get("x", 0)
            y = placement.get("y", 0)
            rot = placement.get("rotation", 0)
            
            # Convert user coordinates (relative to board) to absolute KiCad coordinates
            abs_x = x + board_origin_x
            abs_y = y + board_origin_y
            
            try:
                success = update_footprint_position(
                    pcb_path=pcb_path,
                    reference=ref,
                    new_position=(abs_x, abs_y),
                    new_rotation=rot
                )
                
                if success:
                    results["success"].append({
                        "reference": ref,
                        "position": (x, y),
                        "absolute_position": (abs_x, abs_y),
                        "rotation": rot
                    })
                else:
                    results["failed"].append({
                        "reference": ref,
                        "reason": "Component not found in PCB"
                    })
            except Exception as e:
                results["failed"].append({
                    "reference": ref,
                    "reason": str(e)
                })
        
        return {
            "total": len(placements),
            "placed": len(results["success"]),
            "failed": len(results["failed"]),
            "details": results
        }

    # @mcp.tool()  # Disabled - use KiCad GUI instead
    def add_trace(
        project_path: str,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        net_name: str,
        width: float = 0.25,
        layer: str = "F.Cu"
    ) -> dict[str, Any]:
        """Add a trace (track) between two points on the PCB.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            start_x: Starting X position in mm
            start_y: Starting Y position in mm
            end_x: Ending X position in mm
            end_y: Ending Y position in mm
            net_name: Name of the net (e.g., "GND", "VCC", "SDA")
            width: Trace width in mm (default 0.25)
            layer: PCB layer ("F.Cu" or "B.Cu")
            
        Returns:
            Result of the trace operation
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        
        try:
            pcb = parse_pcb_file(pcb_path)
            net_id = get_net_id_by_name(pcb, net_name)
            
            if net_id is None:
                return {"error": f"Net '{net_name}' not found in PCB"}
            
            track = Track(
                start=(start_x, start_y),
                end=(end_x, end_y),
                width=width,
                layer=layer,
                net=net_id
            )
            
            success = add_track_to_pcb(pcb_path, track)
            
            if success:
                return {
                    "success": True,
                    "message": f"Added trace on {layer} for net {net_name}",
                    "start": (start_x, start_y),
                    "end": (end_x, end_y),
                    "width": width,
                    "net": net_name
                }
            else:
                return {"error": "Failed to add trace"}
        except Exception as e:
            logger.error(f"Error adding trace: {e}")
            return {"error": str(e)}

    # @mcp.tool()  # Disabled - use KiCad GUI instead
    def add_via(
        project_path: str,
        x: float,
        y: float,
        net_name: str,
        size: float = 0.8,
        drill: float = 0.4
    ) -> dict[str, Any]:
        """Add a via at a specific position on the PCB.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            x: X position in mm
            y: Y position in mm
            net_name: Name of the net
            size: Via pad size in mm (default 0.8)
            drill: Drill hole size in mm (default 0.4)
            
        Returns:
            Result of the via operation
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found in project"}
        
        pcb_path = files["pcb"]
        
        try:
            pcb = parse_pcb_file(pcb_path)
            net_id = get_net_id_by_name(pcb, net_name)
            
            if net_id is None:
                return {"error": f"Net '{net_name}' not found in PCB"}
            
            via = Via(
                position=(x, y),
                size=size,
                drill=drill,
                net=net_id
            )
            
            success = add_via_to_pcb(pcb_path, via)
            
            if success:
                return {
                    "success": True,
                    "message": f"Added via at ({x}, {y}) for net {net_name}",
                    "position": (x, y),
                    "net": net_name
                }
            else:
                return {"error": "Failed to add via"}
        except Exception as e:
            logger.error(f"Error adding via: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def suggest_component_placement(
        project_path: str
    ) -> dict[str, Any]:
        """Suggest optimal component placement based on schematic groupings.
        
        Analyzes the schematic netlist and suggests positions for components
        based on their functional groupings and connections.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Suggested placements organized by functional group
        """
        files = get_project_files(project_path)
        
        if "schematic" not in files:
            return {"error": "Schematic file not found"}
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        try:
            # Parse netlist to understand component connections
            netlist = extract_netlist(files["schematic"])
            pcb = parse_pcb_file(files["pcb"])
            bounds = get_board_bounds(pcb)
            
            board_width = bounds[2] - bounds[0]
            board_height = bounds[3] - bounds[1]
            
            # Categorize components by type/function
            categories = {
                "mcu": [],      # Microcontrollers
                "power": [],    # Regulators, inductors
                "sensors": [],  # PPG, IMU
                "passives": [], # Caps, resistors
                "connectors": [],  # USB, debug
                "rf": [],       # Antenna, matching
                "other": []
            }
            
            components = netlist.get("components", [])
            
            for comp in components:
                ref = comp.get("reference", "")
                value = comp.get("value", "").lower()
                footprint = comp.get("footprint", "").lower()
                
                # Categorize by reference prefix and footprint
                if ref.startswith("U"):
                    if "nrf" in value or "mcu" in footprint or "qfn-73" in footprint:
                        categories["mcu"].append(comp)
                    elif "afe" in value or "ppg" in footprint:
                        categories["sensors"].append(comp)
                    elif "lsm" in value or "imu" in footprint:
                        categories["sensors"].append(comp)
                    elif "ldo" in value or "regulator" in footprint:
                        categories["power"].append(comp)
                    else:
                        categories["other"].append(comp)
                elif ref.startswith(("L", "FB")):
                    categories["power"].append(comp)
                elif ref.startswith(("C", "R")):
                    categories["passives"].append(comp)
                elif ref.startswith("J"):
                    categories["connectors"].append(comp)
                elif ref.startswith(("Y", "ANT")):
                    categories["rf"].append(comp)
                else:
                    categories["other"].append(comp)
            
            # Generate placement suggestions based on typical PCB layout
            suggestions = {
                "board_size": {"width": board_width, "height": board_height},
                "groups": {}
            }
            
            # MCU in center
            if categories["mcu"]:
                suggestions["groups"]["mcu"] = {
                    "zone": {"x": board_width * 0.4, "y": board_height * 0.5, "description": "Center of board"},
                    "components": [
                        {"reference": c["reference"], "suggested_x": board_width * 0.4, "suggested_y": board_height * 0.5}
                        for c in categories["mcu"]
                    ]
                }
            
            # Power section (top-left, near USB)
            if categories["power"]:
                suggestions["groups"]["power"] = {
                    "zone": {"x": board_width * 0.15, "y": board_height * 0.3, "description": "Top-left, near power input"},
                    "components": [
                        {"reference": c["reference"], "suggested_x": board_width * 0.1 + i * 3, "suggested_y": board_height * 0.3}
                        for i, c in enumerate(categories["power"])
                    ]
                }
            
            # Sensors (bottom, near skin contact)
            if categories["sensors"]:
                suggestions["groups"]["sensors"] = {
                    "zone": {"x": board_width * 0.5, "y": board_height * 0.8, "description": "Bottom center, facing skin"},
                    "components": [
                        {"reference": c["reference"], "suggested_x": board_width * 0.3 + i * 8, "suggested_y": board_height * 0.8}
                        for i, c in enumerate(categories["sensors"])
                    ]
                }
            
            # RF section (right side, near antenna)
            if categories["rf"]:
                suggestions["groups"]["rf"] = {
                    "zone": {"x": board_width * 0.85, "y": board_height * 0.5, "description": "Right edge, antenna keepout"},
                    "components": [
                        {"reference": c["reference"], "suggested_x": board_width * 0.85, "suggested_y": board_height * 0.3 + i * 3}
                        for i, c in enumerate(categories["rf"])
                    ]
                }
            
            # Connectors (edges)
            if categories["connectors"]:
                suggestions["groups"]["connectors"] = {
                    "zone": {"x": board_width * 0.95, "y": board_height * 0.5, "description": "Board edge"},
                    "components": [
                        {"reference": c["reference"], "suggested_x": board_width * 0.95, "suggested_y": board_height * 0.4 + i * 4}
                        for i, c in enumerate(categories["connectors"])
                    ]
                }
            
            # Passives distributed around their associated ICs
            if categories["passives"]:
                suggestions["groups"]["passives"] = {
                    "zone": {"description": "Near associated ICs - place decoupling caps within 2mm of IC power pins"},
                    "components": [{"reference": c["reference"]} for c in categories["passives"]],
                    "note": "Passives should be placed near their connected IC. Use net connections to determine associations."
                }
            
            return suggestions
            
        except Exception as e:
            logger.error(f"Error generating placement suggestions: {e}")
            return {"error": str(e)}

    @mcp.tool()
    def auto_place_decoupling_caps(
        project_path: str,
        ic_reference: str,
        cap_distance: float = 1.5
    ) -> dict[str, Any]:
        """Automatically place decoupling capacitors near an IC.
        
        Finds capacitors connected to the same power nets as the IC and places
        them at optimal positions around the IC.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            ic_reference: Reference of the IC (e.g., "U1")
            cap_distance: Distance from IC center to place caps (mm)
            
        Returns:
            List of placed capacitors
        """
        files = get_project_files(project_path)
        
        if "schematic" not in files or "pcb" not in files:
            return {"error": "Schematic or PCB file not found"}
        
        try:
            netlist = extract_netlist(files["schematic"])
            pcb = parse_pcb_file(files["pcb"])
            
            # Find the IC
            ic_fp = None
            for fp in pcb.footprints:
                if fp.reference == ic_reference:
                    ic_fp = fp
                    break
            
            if not ic_fp:
                return {"error": f"IC {ic_reference} not found in PCB"}
            
            ic_x, ic_y = ic_fp.position
            
            # Find power nets connected to this IC
            power_nets = set()
            components = netlist.get("components", [])
            nets = netlist.get("nets", {})
            
            for net_name, net_data in nets.items():
                if net_name in ("VCC", "GND", "V1V8", "VBAT", "VUSB", "DCC"):
                    pins = net_data.get("pins", [])
                    for pin in pins:
                        if pin.get("ref") == ic_reference:
                            power_nets.add(net_name)
            
            # Find capacitors on these power nets
            decoupling_caps = []
            for net_name, net_data in nets.items():
                if net_name in power_nets or net_name == "GND":
                    pins = net_data.get("pins", [])
                    for pin in pins:
                        ref = pin.get("ref", "")
                        if ref.startswith("C") and ref != ic_reference:
                            decoupling_caps.append(ref)
            
            # Remove duplicates
            decoupling_caps = list(set(decoupling_caps))
            
            # Place caps around the IC
            placed = []
            angles = [0, 90, 180, 270, 45, 135, 225, 315]
            
            for i, cap_ref in enumerate(decoupling_caps[:8]):  # Max 8 positions
                import math
                angle = math.radians(angles[i % len(angles)])
                cap_x = ic_x + cap_distance * math.cos(angle)
                cap_y = ic_y + cap_distance * math.sin(angle)
                
                success = update_footprint_position(
                    pcb_path=files["pcb"],
                    reference=cap_ref,
                    new_position=(cap_x, cap_y),
                    new_rotation=angles[i % len(angles)] % 180  # Align with IC
                )
                
                if success:
                    placed.append({
                        "reference": cap_ref,
                        "position": (round(cap_x, 2), round(cap_y, 2)),
                        "angle": angles[i % len(angles)]
                    })
            
            return {
                "ic": ic_reference,
                "ic_position": ic_fp.position,
                "power_nets": list(power_nets),
                "placed_caps": placed,
                "total_placed": len(placed)
            }
            
        except Exception as e:
            logger.error(f"Error auto-placing decoupling caps: {e}")
            return {"error": str(e)}

    # NOTE: update_pcb_from_schematic removed - KiCad CLI doesn't have 'pcb update' command
    # Users must do this step manually in KiCad GUI: Tools → Update PCB from Schematic (F8)


# NOTE: import_footprints removed - it can't actually load real footprints from KiCad libraries
# The only way to properly import footprints is through KiCad GUI (F8)


# Make sure to register both tool sets
def register_pcb_tools(mcp: FastMCP) -> None:
    """Register all PCB tools with the MCP server."""
    _register_pcb_layout_tools(mcp)
