"""
Advanced routing tools for KiCad PCB design.

Provides intelligent auto-routing, copper pours, and net-aware routing
capabilities through the MCP interface.
"""

import os
import re
import math
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from collections import defaultdict

from kicad_mcp.utils.pcb_parser import (
    parse_pcb_file, PCBData, Track, Via,
    create_track_sexpr, create_via_sexpr, generate_uuid
)
from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import extract_netlist

logger = logging.getLogger(__name__)


# ============================================================================
# PCB File Helpers
# ============================================================================

def insert_tracks_into_pcb(pcb_path: str, tracks_sexpr: List[str]) -> bool:
    """Safely insert track segments into PCB file before final closing paren.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        tracks_sexpr: List of track S-expressions to insert
        
    Returns:
        True if successful
    """
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the last closing paren (end of kicad_pcb)
    # We need to ensure we're inserting properly
    lines = content.rstrip().rstrip(')').rstrip()
    
    # Build new tracks with proper formatting
    tracks_block = '\n\n\t' + '\n\t'.join(tracks_sexpr)
    
    # Reconstruct the file
    new_content = lines + tracks_block + '\n)'
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return True


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class Pad:
    """Represents a component pad on the PCB."""
    reference: str  # Component reference (e.g., "U1")
    pad_number: str  # Pad number or name
    position: Tuple[float, float]
    size: Tuple[float, float]  # Width, height
    net: str
    layer: str = "F.Cu"
    shape: str = "rect"  # rect, circle, oval


# ============================================================================
# Pad Extraction
# ============================================================================

def extract_pads_from_pcb(pcb: PCBData) -> List[Pad]:
    """Extract all pad positions from PCB footprints.
    
    Args:
        pcb: Parsed PCB data
        
    Returns:
        List of Pad objects with absolute positions
    """
    pads = []
    
    for fp in pcb.footprints:
        # Parse pads from footprint's raw s-expression
        fp_x, fp_y = fp.position
        fp_rot = fp.rotation
        
        # Find all pads in the footprint - extract each pad block
        pad_starts = [m.start() for m in re.finditer(r'\(pad\s+"?[^"\s]+"?\s+', fp.raw_sexpr)]
        
        for start in pad_starts:
            # Find matching closing paren for this pad
            depth = 0
            end = start
            for i in range(start, len(fp.raw_sexpr)):
                if fp.raw_sexpr[i] == '(':
                    depth += 1
                elif fp.raw_sexpr[i] == ')':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            
            pad_text = fp.raw_sexpr[start:end]
            
            # Extract pad number
            pad_num_match = re.search(r'\(pad\s+"?([^"\s]+)"?\s+', pad_text)
            if not pad_num_match:
                continue
            pad_num = pad_num_match.group(1)
            
            # Extract pad type and shape
            type_match = re.search(r'\(pad\s+"?[^"\s]+"?\s+(\w+)\s+(\w+)', pad_text)
            pad_type = type_match.group(1) if type_match else "smd"
            pad_shape = type_match.group(2) if type_match else "rect"
            
            # Extract position relative to footprint
            at_match = re.search(r'\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)', pad_text)
            if not at_match:
                continue
            rel_x = float(at_match.group(1))
            rel_y = float(at_match.group(2))
            
            # Extract size
            size_match = re.search(r'\(size\s+([\d.-]+)\s+([\d.-]+)\)', pad_text)
            if size_match:
                size_x = float(size_match.group(1))
                size_y = float(size_match.group(2))
            else:
                size_x, size_y = 0.5, 0.5
            
            # Calculate absolute position (rotate relative position by footprint rotation)
            # KiCad uses clockwise rotation, so negate the angle
            rot_rad = math.radians(-fp_rot)
            abs_x = fp_x + rel_x * math.cos(rot_rad) - rel_y * math.sin(rot_rad)
            abs_y = fp_y + rel_x * math.sin(rot_rad) + rel_y * math.cos(rot_rad)
            
            # Extract net from pad
            net_match = re.search(r'\(net\s+\d+\s+"([^"]+)"\)', pad_text)
            net_name = net_match.group(1) if net_match else ""
            
            # Extract layers
            layers_match = re.search(r'\(layers\s+"([^"]+)"', pad_text)
            layer = "F.Cu"
            if layers_match:
                if "B.Cu" in layers_match.group(1):
                    layer = "B.Cu"
            
            pads.append(Pad(
                reference=fp.reference,
                pad_number=pad_num,
                position=(abs_x, abs_y),
                size=(size_x, size_y),
                net=net_name,
                layer=layer,
                shape=pad_shape
            ))
    
    return pads


def get_net_pads(pads: List[Pad], net_name: str) -> List[Pad]:
    """Get all pads belonging to a specific net.
    
    Args:
        pads: List of all pads
        net_name: Name of the net
        
    Returns:
        List of pads on this net
    """
    return [p for p in pads if p.net == net_name]


# ============================================================================
# Copper Pour / Zone Creation
# ============================================================================

def create_zone_sexpr(
    net_name: str,
    net_id: int,
    layer: str,
    outline: List[Tuple[float, float]],
    priority: int = 0,
    min_thickness: float = 0.25,
    thermal_gap: float = 0.5,
    thermal_bridge_width: float = 0.5
) -> str:
    """Generate S-expression for a copper zone (pour).
    
    Args:
        net_name: Name of the net (e.g., "GND")
        net_id: Net ID number
        layer: Layer name (e.g., "B.Cu")
        outline: List of (x, y) points defining the zone boundary
        priority: Fill priority (higher = fills first)
        min_thickness: Minimum trace width in zone
        thermal_gap: Gap for thermal relief
        thermal_bridge_width: Width of thermal spokes
        
    Returns:
        S-expression string for the zone
    """
    uuid = generate_uuid()
    
    # Build polygon points
    pts_str = "\n\t\t\t".join(f"(xy {x} {y})" for x, y in outline)
    
    return f'''(zone
		(net {net_id})
		(net_name "{net_name}")
		(layer "{layer}")
		(uuid "{uuid}")
		(hatch edge 0.5)
		(priority {priority})
		(connect_pads
			(clearance 0.3)
		)
		(min_thickness {min_thickness})
		(fill yes
			(thermal_gap {thermal_gap})
			(thermal_bridge_width {thermal_bridge_width})
		)
		(polygon
			(pts
				{pts_str}
			)
		)
	)'''


def add_zone_to_pcb(pcb_path: str, zone_sexpr: str) -> bool:
    """Add a zone to the PCB file.
    
    Args:
        pcb_path: Path to PCB file
        zone_sexpr: Zone S-expression
        
    Returns:
        True if successful
    """
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Insert before the final closing parenthesis
    insert_pos = content.rfind(')')
    if insert_pos == -1:
        return False
    
    new_content = content[:insert_pos] + '\n\t' + zone_sexpr + '\n' + content[insert_pos:]
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return True


# ============================================================================
# MCP Tool Registration
# ============================================================================

def register_routing_tools(mcp):
    """Register routing tools with the MCP server."""
    
    # Import advanced router
    try:
        from kicad_mcp.tools.advanced_router import (
            build_routing_context, route_single_net, segments_to_sexpr,
            categorize_nets, get_net_priority, Point2D, update_context_with_segments,
            DEFAULT_CLEARANCE, DEFAULT_TRACE_WIDTH, DEFAULT_GRID_STEP
        )
        ADVANCED_ROUTER_AVAILABLE = True
        logger.info("Advanced router module loaded successfully")
    except ImportError as e:
        ADVANCED_ROUTER_AVAILABLE = False
        logger.warning(f"Advanced router not available: {e}")
    
    # @mcp.tool()  # Disabled - use freeroute_pcb instead
    def auto_route_net(
        project_path: str,
        net_name: str,
        trace_width: float = 0.25,
        grid_step: float = 0.5,
        min_clearance: float = 0.15
    ) -> Dict[str, Any]:
        """Automatically route a single net using advanced pathfinding.
        
        Finds all pads belonging to the specified net and routes traces
        to connect them using minimum spanning tree approach. Verifies
        clearance from other nets' pads and existing traces.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            net_name: Name of the net to route (e.g., "GND", "VCC", "SDA")
            trace_width: Width of traces in mm (default 0.25)
            grid_step: Routing grid resolution in mm (default 0.5)
            min_clearance: Minimum clearance from obstacles in mm (default 0.15)
            
        Returns:
            Dictionary with routing results
        """
        if not ADVANCED_ROUTER_AVAILABLE:
            return {"error": "Advanced router not available. Consider using Freerouting for auto-routing."}
        
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        try:
            # Parse PCB
            pcb = parse_pcb_file(pcb_path)
            
            # Extract pads
            pads = extract_pads_from_pcb(pcb)
            logger.info(f"Extracted {len(pads)} pads from PCB")
            
            # Build routing context
            ctx = build_routing_context(pcb, pads, exclude_nets=[net_name])
            
            # Route the net
            segments = route_single_net(
                net_name, pcb, pads, ctx, 
                trace_width=trace_width,
                grid_step=grid_step,
                clearance=min_clearance
            )
            
            if not segments:
                return {
                    "success": False,
                    "message": f"Could not route net {net_name}",
                    "traces_added": 0
                }
            
            # Convert to S-expressions
            traces_sexpr = segments_to_sexpr(segments)
            
            # Insert tracks safely into PCB file
            insert_tracks_into_pcb(pcb_path, traces_sexpr)
            
            return {
                "success": True,
                "message": f"Routed net {net_name}",
                "traces_added": len(segments),
                "net": net_name
            }
            
        except Exception as e:
            logger.error(f"Error routing net: {e}")
            return {"error": str(e)}
    
    @mcp.tool()
    def add_ground_pour(
        project_path: str,
        layer: str = "B.Cu",
        net_name: str = "GND",
        margin: float = 0.5
    ) -> Dict[str, Any]:
        """Add a ground pour (copper fill) to a layer.
        
        Creates a copper zone covering the entire board on the specified
        layer, connected to the ground net.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            layer: Layer for the pour (default "B.Cu" for bottom)
            net_name: Net name for the pour (default "GND")
            margin: Margin from board edge in mm (default 0.5)
            
        Returns:
            Dictionary with result
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        try:
            pcb = parse_pcb_file(pcb_path)
            
            # Find net ID
            net_id = None
            for nid, nname in pcb.nets.items():
                if nname == net_name:
                    net_id = nid
                    break
            
            if net_id is None:
                return {"error": f"Net '{net_name}' not found in PCB"}
            
            # Get board outline
            if not pcb.board_outline:
                return {"error": "Board outline not found"}
            
            # Create zone outline with margin
            xs = [p[0] for p in pcb.board_outline]
            ys = [p[1] for p in pcb.board_outline]
            min_x, max_x = min(xs) + margin, max(xs) - margin
            min_y, max_y = min(ys) + margin, max(ys) - margin
            
            outline = [
                (min_x, min_y),
                (max_x, min_y),
                (max_x, max_y),
                (min_x, max_y),
            ]
            
            zone_sexpr = create_zone_sexpr(
                net_name=net_name,
                net_id=net_id,
                layer=layer,
                outline=outline,
                priority=0
            )
            
            success = add_zone_to_pcb(pcb_path, zone_sexpr)
            
            if success:
                return {
                    "success": True,
                    "message": f"Added {net_name} pour on {layer}",
                    "layer": layer,
                    "net": net_name,
                    "bounds": {"x1": min_x, "y1": min_y, "x2": max_x, "y2": max_y}
                }
            else:
                return {"error": "Failed to add zone to PCB"}
                
        except Exception as e:
            logger.error(f"Error adding ground pour: {e}")
            return {"error": str(e)}
    
    # @mcp.tool()  # Disabled - use freeroute_pcb instead
    def route_all_nets(
        project_path: str,
        trace_width: float = 0.25,
        power_trace_width: float = 0.4,
        skip_nets: Optional[List[str]] = None,
        min_clearance: float = 0.15
    ) -> Dict[str, Any]:
        """Route all nets in the PCB automatically.
        
        Routes each net in priority order: power nets first, then signals.
        Skips GND net (should use ground pour instead).
        
        Note: For better routing quality, consider using Freerouting via 
        the freeroute_pcb tool which uses professional push-and-shove routing.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            trace_width: Width for signal traces (mm)
            power_trace_width: Width for power traces (mm)
            skip_nets: List of net names to skip
            min_clearance: Minimum clearance between traces and obstacles (mm)
            
        Returns:
            Dictionary with routing summary
        """
        if not ADVANCED_ROUTER_AVAILABLE:
            return {"error": "Advanced router not available. Consider using Freerouting (freeroute_pcb) for auto-routing."}
        
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        if skip_nets is None:
            skip_nets = ["GND", ""]  # Skip ground (use pour) and unnamed nets
        
        try:
            pcb = parse_pcb_file(pcb_path)
            pads = extract_pads_from_pcb(pcb)
            
            # Build routing context
            ctx = build_routing_context(pcb, pads, clearance=min_clearance)
            
            # Categorize nets
            net_cats = categorize_nets(pcb.nets, skip_nets)
            
            results = {
                "routed": [],
                "failed": [],
                "skipped": list(skip_nets)
            }
            
            logger.info(f"Routing with clearance={min_clearance}mm")
            
            # Route power nets first
            for i, net_name in enumerate(net_cats.get('power', [])):
                net_pads = get_net_pads(pads, net_name)
                if len(net_pads) < 2:
                    results["skipped"].append(net_name)
                    continue
                
                logger.info(f"Routing power net {i+1}: {net_name} ({len(net_pads)} pads)")
                
                segments = route_single_net(
                    net_name, pcb, pads, ctx,
                    trace_width=power_trace_width,
                    grid_step=0.5,
                    clearance=min_clearance
                )
                
                if segments:
                    traces_sexpr = segments_to_sexpr(segments)
                    insert_tracks_into_pcb(pcb_path, traces_sexpr)
                    update_context_with_segments(ctx, segments)
                    results["routed"].append({"net": net_name, "traces": len(segments), "type": "power"})
                    pcb = parse_pcb_file(pcb_path)
                else:
                    results["failed"].append(net_name)
            
            # Route signal nets
            for i, net_name in enumerate(net_cats.get('signal', [])):
                net_pads = get_net_pads(pads, net_name)
                if len(net_pads) < 2 or net_name.startswith('unconnected-'):
                    results["skipped"].append(net_name)
                    continue
                
                logger.info(f"Routing signal net {i+1}: {net_name} ({len(net_pads)} pads)")
                
                segments = route_single_net(
                    net_name, pcb, pads, ctx,
                    trace_width=trace_width,
                    grid_step=0.5,
                    clearance=min_clearance
                )
                
                if segments:
                    traces_sexpr = segments_to_sexpr(segments)
                    insert_tracks_into_pcb(pcb_path, traces_sexpr)
                    update_context_with_segments(ctx, segments)
                    results["routed"].append({"net": net_name, "traces": len(segments), "type": "signal"})
                    pcb = parse_pcb_file(pcb_path)
                else:
                    results["failed"].append(net_name)
            
            return {
                "success": True,
                "summary": {
                    "nets_routed": len(results["routed"]),
                    "nets_failed": len(results["failed"]),
                    "nets_skipped": len(results["skipped"])
                },
                "details": results
            }
            
        except Exception as e:
            logger.error(f"Error in auto-routing: {e}")
            return {"error": str(e)}
    
    # smart_route_all removed - was buggy and hanging
    
    @mcp.tool()
    def analyze_board_for_routing(project_path: str) -> Dict[str, Any]:
        """Analyze board complexity and get routing recommendations.
        
        This tool examines the PCB layout and provides:
        - Board density metrics (pads per mm², congestion factor)
        - Recommended number of layers
        - Optimal routing parameters (grid, trace width, clearance)
        - Routing difficulty assessment
        
        Use this BEFORE routing to understand the board and get optimal parameters.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with board analysis and recommendations
        """
        if not ADVANCED_ROUTER_AVAILABLE:
            return {"error": "Advanced router module not available"}
        
        # Import analysis functions
        from kicad_mcp.tools.advanced_router import (
            analyze_board_complexity, get_adaptive_routing_params, format_analysis_report
        )
        
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        try:
            pcb = parse_pcb_file(pcb_path)
            pads = extract_pads_from_pcb(pcb)
            
            # Run analysis
            analysis = analyze_board_complexity(pcb, pads)
            params = get_adaptive_routing_params(analysis)
            
            return {
                "success": True,
                "board": {
                    "dimensions": f"{analysis.board_width} x {analysis.board_height} mm",
                    "area_mm2": analysis.board_area_mm2,
                    "components": analysis.component_count,
                    "total_pads": analysis.total_pads,
                    "pad_density": round(analysis.pad_density, 4)
                },
                "routing_metrics": {
                    "total_nets": analysis.total_nets,
                    "routable_nets": analysis.routable_nets,
                    "total_connections": analysis.total_connections,
                    "congestion_factor": round(analysis.congestion_factor, 2),
                    "difficulty": analysis.routing_difficulty
                },
                "recommendations": {
                    "layers": analysis.layer_recommendation,
                    "grid_step_mm": analysis.recommended_grid,
                    "trace_width_mm": analysis.recommended_trace_width,
                    "clearance_mm": analysis.recommended_clearance,
                    "max_iterations": params["max_iterations"],
                    "heuristic_weight": params["heuristic_weight"]
                },
                "layer_explanation": _get_layer_explanation(analysis.layer_recommendation, analysis),
                "report": format_analysis_report(analysis)
            }
            
        except Exception as e:
            import traceback
            logger.error(f"Error analyzing board: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}
    
    def _get_layer_explanation(layers: int, analysis) -> str:
        """Explain why a certain layer count is recommended."""
        if layers == 2:
            return (f"2 layers sufficient: Low congestion ({analysis.congestion_factor:.0%}), "
                   f"moderate density ({analysis.pad_density:.3f} pads/mm²). "
                   "Top for signals, bottom for ground pour.")
        elif layers == 4:
            return (f"4 layers recommended: Moderate congestion ({analysis.congestion_factor:.0%}). "
                   "Typical stack: Signal-GND-Power-Signal. "
                   "Inner planes provide shielding and power distribution.")
        else:
            return (f"6+ layers needed: High congestion ({analysis.congestion_factor:.0%}), "
                   f"dense layout ({analysis.pad_density:.3f} pads/mm²). "
                   "Consider: Sig-GND-Sig-Sig-Power-Sig for complex routing.")

    @mcp.tool()
    def get_unrouted_nets(project_path: str) -> Dict[str, Any]:
        """Get a list of nets that need to be routed.
        
        Analyzes the PCB to find nets with multiple pads that don't
        have traces connecting them yet.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with unrouted nets and their pad counts
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        try:
            pcb = parse_pcb_file(pcb_path=files["pcb"])
            pads = extract_pads_from_pcb(pcb)
            
            # Count pads per net
            net_pads = defaultdict(list)
            for pad in pads:
                if pad.net:
                    net_pads[pad.net].append(pad.reference + "." + pad.pad_number)
            
            # Check which nets have traces
            routed_nets = set()
            for track in pcb.tracks:
                net_name = pcb.nets.get(track.net, "")
                if net_name:
                    routed_nets.add(net_name)
            
            # Build result
            unrouted = []
            partially_routed = []
            
            for net_name, pad_list in net_pads.items():
                if len(pad_list) < 2:
                    continue  # Single-pad nets don't need routing
                
                if net_name in routed_nets:
                    partially_routed.append({
                        "net": net_name,
                        "pads": len(pad_list),
                        "pad_list": pad_list[:5]  # First 5 pads
                    })
                else:
                    unrouted.append({
                        "net": net_name,
                        "pads": len(pad_list),
                        "pad_list": pad_list[:5]
                    })
            
            # Sort by pad count (route simpler nets first)
            unrouted.sort(key=lambda x: x["pads"])
            
            return {
                "unrouted_count": len(unrouted),
                "partially_routed_count": len(partially_routed),
                "unrouted": unrouted,
                "partially_routed": partially_routed,
                "total_nets": len(net_pads)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing nets: {e}")
            return {"error": str(e)}
    
    # @mcp.tool()  # Disabled - niche feature
    def add_via_stitching(
        project_path: str,
        net_name: str = "GND",
        spacing: float = 5.0,
        via_size: float = 0.8,
        via_drill: float = 0.4
    ) -> Dict[str, Any]:
        """Add via stitching between top and bottom copper pours.
        
        Creates a grid of vias connecting ground planes on both layers
        for better EMI performance and thermal conductivity.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            net_name: Net to stitch (default "GND")
            spacing: Distance between vias in mm (default 5.0)
            via_size: Via pad size in mm (default 0.8)
            via_drill: Via drill size in mm (default 0.4)
            
        Returns:
            Dictionary with result
        """
        files = get_project_files(project_path)
        if "pcb" not in files:
            return {"error": "PCB file not found"}
        
        pcb_path = files["pcb"]
        
        try:
            pcb = parse_pcb_file(pcb_path)
            pads = extract_pads_from_pcb(pcb)
            
            # Find net ID
            net_id = None
            for nid, nname in pcb.nets.items():
                if nname == net_name:
                    net_id = nid
                    break
            
            if net_id is None:
                return {"error": f"Net '{net_name}' not found"}
            
            # Get board bounds
            if not pcb.board_outline:
                return {"error": "Board outline not found"}
            
            xs = [p[0] for p in pcb.board_outline]
            ys = [p[1] for p in pcb.board_outline]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            board_bounds = (min_x, min_y, max_x, max_y)
            
            # Build set of existing obstacles (pads, vias, tracks) for collision detection
            occupied_positions = set()
            clearance = 0.5  # mm clearance around existing objects
            
            # Add pad positions with clearance
            for pad in pads:
                px, py = pad.position
                w, h = pad.size
                # Mark a grid of points around pad as occupied
                for dx in range(-int((w/2 + clearance) * 2), int((w/2 + clearance) * 2) + 1):
                    for dy in range(-int((h/2 + clearance) * 2), int((h/2 + clearance) * 2) + 1):
                        occupied_positions.add((round(px + dx/2, 1), round(py + dy/2, 1)))
            
            # Add via positions
            for via in pcb.vias:
                vx, vy = via.position
                r = via.size / 2 + clearance
                for dx in range(-int(r * 2), int(r * 2) + 1):
                    for dy in range(-int(r * 2), int(r * 2) + 1):
                        occupied_positions.add((round(vx + dx/2, 1), round(vy + dy/2, 1)))
            
            # Generate via positions on grid
            vias_added = []
            margin = 1.5  # Keep away from board edge
            
            x = min_x + margin
            while x < max_x - margin:
                y = min_y + margin
                while y < max_y - margin:
                    # Check if position is clear (not in occupied positions)
                    pos_rounded = (round(x, 1), round(y, 1))
                    if pos_rounded not in occupied_positions:
                        # Additional check: ensure not too close to any pad center
                        too_close = False
                        for pad in pads:
                            px, py = pad.position
                            dist = math.sqrt((x - px)**2 + (y - py)**2)
                            if dist < clearance + via_size/2:
                                too_close = True
                                break
                        
                        if not too_close:
                            vias_added.append((x, y))
                    
                    y += spacing
                x += spacing
            
            # Add vias to PCB
            if vias_added:
                with open(pcb_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                insert_pos = content.rfind(')')
                vias_sexpr = []
                
                for x, y in vias_added:
                    vias_sexpr.append(create_via_sexpr(
                        position=(x, y),
                        size=via_size,
                        drill=via_drill,
                        layers=("F.Cu", "B.Cu"),
                        net=net_id
                    ))
                
                new_content = (content[:insert_pos] + 
                              '\n\t' + '\n\t'.join(vias_sexpr) + '\n' + 
                              content[insert_pos:])
                
                with open(pcb_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
            
            return {
                "success": True,
                "message": f"Added {len(vias_added)} stitching vias for {net_name}",
                "vias_added": len(vias_added),
                "net": net_name,
                "spacing": spacing
            }
            
        except Exception as e:
            logger.error(f"Error adding via stitching: {e}")
            return {"error": str(e)}


# ============================================================================
# Layer Transition Via Detection and Addition
# ============================================================================

def find_layer_transition_points(pcb: PCBData, pads: List[Pad], 
                                  via_size: float = 0.6) -> Dict[str, List[Tuple[float, float]]]:
    """Find points where tracks on different layers for the same net need vias.
    
    Args:
        pcb: Parsed PCB data
        pads: List of all pads for collision checking
        via_size: Diameter of vias being placed (for clearance calculation)
        
    Returns:
        Dictionary mapping net names to list of (x, y) points needing vias
    """
    # Build pad exclusion zones - CRITICALLY IMPORTANT:
    # We need to keep vias away from ALL pads (even same net) to avoid shorts
    # The clearance must account for: pad_half_width + via_radius + trace_clearance
    via_radius = via_size / 2
    min_clearance = 0.25  # Minimum copper-to-copper clearance
    
    # Store ALL pads as exclusion zones (not per-layer - vias span all layers)
    # Format: (center_x, center_y, half_width + exclusion, half_height + exclusion, net)
    all_pad_zones: List[Tuple[float, float, float, float, str]] = []
    
    for pad in pads:
        x, y = pad.position
        w, h = pad.size
        # Total exclusion = pad_half_size + via_radius + clearance
        exclusion_x = w/2 + via_radius + min_clearance
        exclusion_y = h/2 + via_radius + min_clearance
        all_pad_zones.append((x, y, exclusion_x, exclusion_y, pad.net))
    
    def point_hits_any_pad(x: float, y: float, net_name: str) -> bool:
        """Check if a via at (x,y) would hit/short ANY pad.
        
        We check all pads including same-net pads because placing a via
        on top of a pad can still cause manufacturing issues and shorts.
        """
        for px, py, ex, ey, pad_net in all_pad_zones:
            # Check if point is within exclusion zone (rectangular check)
            if abs(x - px) <= ex and abs(y - py) <= ey:
                return True  # Too close - would create short or clearance violation
        return False
    
    # Group track endpoints by net
    net_endpoints: Dict[int, Dict[str, List[Tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    
    for track in pcb.tracks:
        net_endpoints[track.net][track.layer].append(track.start)
        net_endpoints[track.net][track.layer].append(track.end)
    
    # Find points where F.Cu and B.Cu endpoints are close but no via exists
    existing_vias = set()
    for via in pcb.vias:
        existing_vias.add((round(via.position[0], 2), round(via.position[1], 2)))
    
    transition_points: Dict[str, List[Tuple[float, float]]] = {}
    
    for net_id, layer_points in net_endpoints.items():
        net_name = pcb.nets.get(net_id, f"net_{net_id}")
        
        if "F.Cu" not in layer_points or "B.Cu" not in layer_points:
            continue
        
        f_points = set((round(p[0], 2), round(p[1], 2)) for p in layer_points["F.Cu"])
        b_points = set((round(p[0], 2), round(p[1], 2)) for p in layer_points["B.Cu"])
        
        # Find close points (within 0.5mm) that need vias
        needed_vias = []
        for fp in f_points:
            for bp in b_points:
                dist = math.sqrt((fp[0] - bp[0])**2 + (fp[1] - bp[1])**2)
                if dist < 0.5:  # Close enough to need a via
                    # Use midpoint
                    via_pos = ((fp[0] + bp[0]) / 2, (fp[1] + bp[1]) / 2)
                    via_pos_rounded = (round(via_pos[0], 2), round(via_pos[1], 2))
                    
                    # Check if position is clear (not on any pad)
                    if via_pos_rounded not in existing_vias and not point_hits_any_pad(via_pos[0], via_pos[1], net_name):
                        needed_vias.append(via_pos)
                        existing_vias.add(via_pos_rounded)
        
        if needed_vias:
            transition_points[net_name] = needed_vias
    
    return transition_points


def add_layer_transition_vias_to_pcb(pcb_path: str, 
                                     via_size: float = 0.6,
                                     via_drill: float = 0.3) -> Dict[str, Any]:
    """Add vias at layer transition points.
    
    Args:
        pcb_path: Path to PCB file
        via_size: Via pad diameter in mm
        via_drill: Via drill diameter in mm
        
    Returns:
        Result dictionary
    """
    pcb = parse_pcb_file(pcb_path)
    pads = extract_pads_from_pcb(pcb)
    transition_points = find_layer_transition_points(pcb, pads, via_size=via_size)
    
    if not transition_points:
        return {"success": True, "message": "No layer transitions need vias", "vias_added": 0}
    
    vias_sexpr = []
    total_vias = 0
    
    for net_name, points in transition_points.items():
        # Get net ID
        net_id = 0
        for nid, nname in pcb.nets.items():
            if nname == net_name:
                net_id = nid
                break
        
        for x, y in points:
            vias_sexpr.append(create_via_sexpr(
                position=(x, y),
                size=via_size,
                drill=via_drill,
                layers=("F.Cu", "B.Cu"),
                net=net_id
            ))
            total_vias += 1
    
    if vias_sexpr:
        with open(pcb_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        insert_pos = content.rfind(')')
        new_content = (content[:insert_pos] + 
                      '\n\t' + '\n\t'.join(vias_sexpr) + '\n' + 
                      content[insert_pos:])
        
        with open(pcb_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    
    return {
        "success": True,
        "message": f"Added {total_vias} layer transition vias",
        "vias_added": total_vias,
        "nets_fixed": list(transition_points.keys())
    }


# ============================================================================
# Dangling Track Cleanup
# ============================================================================

def find_dangling_tracks(pcb: PCBData, pads: List[Pad]) -> List[str]:
    """Find track UUIDs that have endpoints not connected to anything.
    
    Args:
        pcb: Parsed PCB data
        pads: List of pads
        
    Returns:
        List of track UUIDs that are dangling
    """
    # Build set of valid connection points (pads, via positions, other track endpoints)
    valid_points: Dict[str, Set[Tuple[float, float]]] = defaultdict(set)
    
    # Add pad positions
    for pad in pads:
        valid_points[pad.layer].add((round(pad.position[0], 2), round(pad.position[1], 2)))
    
    # Add via positions (valid on both layers)
    for via in pcb.vias:
        pos = (round(via.position[0], 2), round(via.position[1], 2))
        valid_points["F.Cu"].add(pos)
        valid_points["B.Cu"].add(pos)
    
    # Build connectivity map from tracks
    track_endpoints: Dict[str, List[Tuple[Tuple[float, float], str]]] = defaultdict(list)  # layer -> [(pos, uuid)]
    
    for track in pcb.tracks:
        start = (round(track.start[0], 2), round(track.start[1], 2))
        end = (round(track.end[0], 2), round(track.end[1], 2))
        track_endpoints[track.layer].append((start, track.uuid))
        track_endpoints[track.layer].append((end, track.uuid))
    
    # Add track endpoints as valid connection points
    for layer, endpoints in track_endpoints.items():
        for pos, _ in endpoints:
            valid_points[layer].add(pos)
    
    # Find tracks with endpoints not connecting to other tracks or pads
    dangling_uuids = []
    
    for track in pcb.tracks:
        start = (round(track.start[0], 2), round(track.start[1], 2))
        end = (round(track.end[0], 2), round(track.end[1], 2))
        
        # Count connections for each endpoint
        start_connections = sum(1 for pos, uid in track_endpoints[track.layer] 
                               if pos == start and uid != track.uuid)
        end_connections = sum(1 for pos, uid in track_endpoints[track.layer] 
                             if pos == end and uid != track.uuid)
        
        # Check pad connections
        if start in valid_points[track.layer]:
            start_connections += 1
        if end in valid_points[track.layer]:
            end_connections += 1
        
        # If both ends have no connections (other than to valid points), it's dangling
        if start_connections == 0 or end_connections == 0:
            dangling_uuids.append(track.uuid)
    
    return dangling_uuids


def cleanup_dangling_tracks(pcb_path: str) -> Dict[str, Any]:
    """Remove dangling track segments from PCB.
    
    Args:
        pcb_path: Path to PCB file
        
    Returns:
        Result dictionary
    """
    pcb = parse_pcb_file(pcb_path)
    pads = extract_pads_from_pcb(pcb)
    
    dangling = find_dangling_tracks(pcb, pads)
    
    if not dangling:
        return {"success": True, "message": "No dangling tracks found", "tracks_removed": 0}
    
    # Remove dangling tracks from file using balanced S-expression removal
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    removed = 0
    for uuid in dangling:
        # Find the segment with this UUID using balanced parsing
        uuid_pattern = rf'\(segment\s[^)]*\(uuid\s+"{uuid}"\)'
        match = re.search(uuid_pattern, content)
        if match:
            # Find the actual start of the segment
            # Search backwards from uuid match to find (segment
            search_start = max(0, match.start() - 500)
            search_region = content[search_start:match.end()]
            
            # Find all (segment occurrences and take the last one
            segment_matches = list(re.finditer(r'\(segment\s', search_region))
            if segment_matches:
                segment_start = search_start + segment_matches[-1].start()
                segment_end = _find_balanced_sexp(content, segment_start)
                
                if segment_end != -1:
                    # Remove the segment and trailing whitespace
                    while segment_end < len(content) and content[segment_end] in ' \t\n\r':
                        segment_end += 1
                    content = content[:segment_start] + content[segment_end:]
                    removed += 1
    
    # Verify balance
    opens = content.count('(')
    closes = content.count(')')
    if opens != closes:
        logger.error(f"File would become unbalanced after cleanup: {opens} opens, {closes} closes")
        return {
            "success": False,
            "error": f"Operation would corrupt file (unbalanced parentheses)",
            "tracks_found": len(dangling)
        }
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return {
        "success": True,
        "message": f"Removed {removed} dangling tracks",
        "tracks_removed": removed
    }


def _find_balanced_sexp(content: str, start_idx: int) -> int:
    """Find the end index of a balanced S-expression starting at start_idx.
    
    Args:
        content: The full text content
        start_idx: Index of opening '('
        
    Returns:
        Index after the closing ')' of this S-expression, or -1 if unbalanced
    """
    if start_idx >= len(content) or content[start_idx] != '(':
        return -1
    
    depth = 0
    in_string = False
    i = start_idx
    
    while i < len(content):
        char = content[i]
        
        if char == '"' and (i == 0 or content[i-1] != '\\'):
            in_string = not in_string
        elif not in_string:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    
    return -1  # Unbalanced


def _remove_sexps_by_type(content: str, sexp_type: str) -> tuple[str, int]:
    """Remove all S-expressions of a given type from content.
    
    Args:
        content: The PCB file content
        sexp_type: The type to remove (e.g., 'segment', 'via')
        
    Returns:
        Tuple of (new_content, count_removed)
    """
    pattern = rf'\({sexp_type}\s'
    removed = 0
    result = []
    last_end = 0
    
    for match in re.finditer(pattern, content):
        start = match.start()
        end = _find_balanced_sexp(content, start)
        
        if end == -1:
            # Couldn't find balanced end, skip this match
            continue
        
        # Add content before this S-expression
        result.append(content[last_end:start])
        
        # Skip any trailing whitespace/newlines after the S-expression
        while end < len(content) and content[end] in ' \t\n\r':
            end += 1
        
        last_end = end
        removed += 1
    
    # Add remaining content
    result.append(content[last_end:])
    
    return ''.join(result), removed


def clear_all_tracks_from_pcb(pcb_path: str, keep_zones: bool = True) -> Dict[str, Any]:
    """Clear all tracks and vias from PCB for fresh routing.
    
    Args:
        pcb_path: Path to PCB file
        keep_zones: If True, preserve copper zones/pours
        
    Returns:
        Result dictionary with removal counts
    """
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_length = len(content)
    
    # Verify file is balanced before modification
    original_opens = content.count('(')
    original_closes = content.count(')')
    if original_opens != original_closes:
        logger.warning(f"PCB file already unbalanced: {original_opens} opens, {original_closes} closes")
    
    # Remove track segments using balanced S-expression parser
    content, tracks_removed = _remove_sexps_by_type(content, 'segment')
    
    # Remove vias using balanced S-expression parser
    content, vias_removed = _remove_sexps_by_type(content, 'via')
    
    # Verify balance after modification
    new_opens = content.count('(')
    new_closes = content.count(')')
    if new_opens != new_closes:
        logger.error(f"PCB file became unbalanced after modification: {new_opens} opens, {new_closes} closes")
        return {
            "success": False,
            "error": f"Operation would corrupt file (unbalanced parentheses: {new_opens} opens, {new_closes} closes)",
            "tracks_found": tracks_removed,
            "vias_found": vias_removed
        }
    
    # Optionally clear zone fills (but keep zone definitions)
    zones_info = []
    if keep_zones:
        # Find all zones and report them
        zone_pattern = r'\(zone\s+[^)]*\(net\s+\d+\s+"([^"]+)"\)'
        zone_matches = re.findall(zone_pattern, content)
        zones_info = list(set(zone_matches))
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return {
        "success": True,
        "message": f"Cleared {tracks_removed} tracks and {vias_removed} vias",
        "tracks_removed": tracks_removed,
        "vias_removed": vias_removed,
        "zones_preserved": zones_info if keep_zones else [],
        "bytes_removed": original_length - len(content)
    }


# ============================================================================
# MCP Tool Registration - Additional Tools
# ============================================================================

def register_routing_fix_tools(mcp):
    """Register additional routing fix tools with MCP server."""
    
    # @mcp.tool()  # Disabled - niche feature
    async def add_layer_transition_vias(
        project_path: str,
        via_size: float = 0.6,
        via_drill: float = 0.3
    ) -> Dict[str, Any]:
        """Add vias where tracks on different layers need to connect.
        
        Analyzes the PCB for tracks on F.Cu and B.Cu that belong to the same
        net but don't have vias connecting them.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
            via_size: Via pad diameter in mm (default 0.6)
            via_drill: Via drill diameter in mm (default 0.3)
            
        Returns:
            Result dictionary with vias_added count
        """
        try:
            files = get_project_files(project_path)
            pcb_path = files.get('pcb')
            
            if not pcb_path or not os.path.exists(pcb_path):
                return {"error": "PCB file not found"}
            
            return add_layer_transition_vias_to_pcb(pcb_path, via_size, via_drill)
            
        except Exception as e:
            logger.error(f"Error adding layer transition vias: {e}")
            return {"error": str(e)}
    
    # @mcp.tool()  # Disabled - rarely needed
    async def remove_dangling_tracks(project_path: str) -> Dict[str, Any]:
        """Remove track segments that aren't connected to anything.
        
        Identifies and removes track segments that have endpoints not
        connected to pads, vias, or other tracks.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
            
        Returns:
            Result dictionary with tracks_removed count
        """
        try:
            files = get_project_files(project_path)
            pcb_path = files.get('pcb')
            
            if not pcb_path or not os.path.exists(pcb_path):
                return {"error": "PCB file not found"}
            
            return cleanup_dangling_tracks(pcb_path)
            
        except Exception as e:
            logger.error(f"Error removing dangling tracks: {e}")
            return {"error": str(e)}
    
    # @mcp.tool()  # Disabled - rarely needed
    async def clear_all_tracks(
        project_path: str,
        keep_zones: bool = True
    ) -> Dict[str, Any]:
        """Remove all tracks and vias from PCB for a fresh routing start.
        
        Clears all routed traces and vias to allow for complete re-routing
        with Freerouting or other auto-routers. Optionally keeps copper zones
        (like ground pours) intact.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
            keep_zones: If True, preserves copper zones/pours (default True)
            
        Returns:
            Dictionary with tracks_removed, vias_removed counts
        """
        try:
            files = get_project_files(project_path)
            pcb_path = files.get('pcb')
            
            if not pcb_path or not os.path.exists(pcb_path):
                return {"error": "PCB file not found"}
            
            return clear_all_tracks_from_pcb(pcb_path, keep_zones)
            
        except Exception as e:
            logger.error(f"Error clearing tracks: {e}")
            return {"error": str(e)}
    
    logger.info("Registered routing fix tools: add_layer_transition_vias, remove_dangling_tracks, clear_all_tracks")
    
    logger.info("Registered routing tools: auto_route_net, add_ground_pour, route_all_nets, get_unrouted_nets, add_via_stitching")
