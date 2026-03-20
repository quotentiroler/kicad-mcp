"""
Circuit pattern recognition tools for KiCad schematics.
"""
import os
from typing import Dict, List, Any, Optional
from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import extract_netlist, analyze_netlist
from kicad_mcp.utils.pattern_recognition import (
    identify_power_supplies,
    identify_amplifiers,
    identify_filters,
    identify_oscillators,
    identify_digital_interfaces,
    identify_microcontrollers,
    identify_sensor_interfaces
)

def register_pattern_tools(mcp: FastMCP) -> None:
    """Register circuit pattern recognition tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """
    
    # Internal function - use analyze_project_circuit_patterns instead
    def _identify_circuit_patterns_impl(schematic_path: str) -> Dict[str, Any]:
        """Internal: Identify common circuit patterns in a KiCad schematic."""
        if not os.path.exists(schematic_path):
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}
        
        try:
            print(f"Loading schematic file: {os.path.basename(schematic_path)}")
            print("Parsing schematic structure...")
            
            netlist_data = extract_netlist(schematic_path)
            
            if "error" in netlist_data:
                return {"success": False, "error": netlist_data['error']}
            
            print("Analyzing components and connections...")
            
            components = netlist_data.get("components", {})
            nets = netlist_data.get("nets", {})
            
            print("Identifying circuit patterns...")
            
            identified_patterns = {
                "power_supply_circuits": [],
                "amplifier_circuits": [],
                "filter_circuits": [],
                "oscillator_circuits": [],
                "digital_interface_circuits": [],
                "microcontroller_circuits": [],
                "sensor_interface_circuits": [],
                "other_patterns": []
            }
            
            # Identify all patterns
            identified_patterns["power_supply_circuits"] = identify_power_supplies(components, nets)
            identified_patterns["amplifier_circuits"] = identify_amplifiers(components, nets)
            identified_patterns["filter_circuits"] = identify_filters(components, nets)
            identified_patterns["oscillator_circuits"] = identify_oscillators(components, nets)
            identified_patterns["digital_interface_circuits"] = identify_digital_interfaces(components, nets)
            identified_patterns["microcontroller_circuits"] = identify_microcontrollers(components)
            identified_patterns["sensor_interface_circuits"] = identify_sensor_interfaces(components, nets)
            
            # Build result
            result = {
                "success": True,
                "schematic_path": schematic_path,
                "component_count": netlist_data["component_count"],
                "identified_patterns": identified_patterns
            }
            
            # Count total patterns
            total_patterns = sum(len(patterns) for patterns in identified_patterns.values())
            result["total_patterns_found"] = total_patterns
            
            print(f"Pattern recognition complete. Found {total_patterns} circuit patterns.")
            return result
            
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def analyze_project_circuit_patterns(project_path: str) -> Dict[str, Any]:
        """Identify circuit patterns in a KiCad project's schematic.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with identified circuit patterns
        """
        if not os.path.exists(project_path):
            return {"success": False, "error": f"Project not found: {project_path}"}
        
        # Get the schematic file
        try:
            files = get_project_files(project_path)
            
            if "schematic" not in files:
                return {"success": False, "error": "Schematic file not found in project"}
            
            schematic_path = files["schematic"]
            print(f"Found schematic file: {os.path.basename(schematic_path)}")
            
            # Identify patterns in the schematic using internal impl
            result = _identify_circuit_patterns_impl(schematic_path)
            
            # Add project path to result
            if "success" in result and result["success"]:
                result["project_path"] = project_path
            
            return result
            
        except Exception as e:
            return {"success": False, "error": str(e)}
