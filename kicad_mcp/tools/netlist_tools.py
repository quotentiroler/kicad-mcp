"""
Netlist extraction and analysis tools for KiCad schematics.
"""
import os
from typing import Dict, Any
from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import extract_netlist, analyze_netlist

def register_netlist_tools(mcp: FastMCP) -> None:
    """Register netlist-related tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """
    
    # Internal function - use extract_project_netlist instead
    def _extract_schematic_netlist_impl(schematic_path: str) -> Dict[str, Any]:
        """Internal: Extract netlist information from a KiCad schematic."""
        print(f"Extracting netlist from schematic: {schematic_path}")
        
        if not os.path.exists(schematic_path):
            print(f"Schematic file not found: {schematic_path}")
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}
        
        # Extract netlist information
        try:
            print("Parsing schematic structure...")
            netlist_data = extract_netlist(schematic_path)
            
            if "error" in netlist_data:
                print(f"Error extracting netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}
            
            print(f"Extracted {netlist_data['component_count']} components and {netlist_data['net_count']} nets")
            
            # Analyze the netlist
            print("Analyzing netlist data...")
            analysis_results = analyze_netlist(netlist_data)
            
            # Build result
            result = {
                "success": True,
                "schematic_path": schematic_path,
                "component_count": netlist_data["component_count"],
                "net_count": netlist_data["net_count"],
                "components": netlist_data["components"],
                "nets": netlist_data["nets"],
                "analysis": analysis_results
            }
            
            print("Netlist extraction complete")
            return result
            
        except Exception as e:
            print(f"Error extracting netlist: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def extract_project_netlist(project_path: str) -> Dict[str, Any]:
        """Extract netlist from a KiCad project's schematic.
        
        This tool finds the schematic associated with a KiCad project
        and extracts its netlist information.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with netlist information
        """
        print(f"Extracting netlist for project: {project_path}")
        
        if not os.path.exists(project_path):
            print(f"Project not found: {project_path}")
            return {"success": False, "error": f"Project not found: {project_path}"}
        
        # Get the schematic file
        try:
            files = get_project_files(project_path)
            
            if "schematic" not in files:
                print("Schematic file not found in project")
                return {"success": False, "error": "Schematic file not found in project"}
            
            schematic_path = files["schematic"]
            print(f"Found schematic file: {schematic_path}")
            
            # Call the internal schematic netlist extraction
            result = _extract_schematic_netlist_impl(schematic_path)
            
            # Add project path to result
            if "success" in result and result["success"]:
                result["project_path"] = project_path
            
            return result
            
        except Exception as e:
            print(f"Error extracting project netlist: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def analyze_schematic_connections(schematic_path: str) -> Dict[str, Any]:
        """Analyze connections in a KiCad schematic.
        
        This tool provides detailed analysis of component connections,
        including power nets, signal paths, and potential issues.
        
        Args:
            schematic_path: Path to the KiCad schematic file (.kicad_sch)
            
        Returns:
            Dictionary with connection analysis
        """
        print(f"Analyzing connections in schematic: {schematic_path}")
        
        if not os.path.exists(schematic_path):
            print(f"Schematic file not found: {schematic_path}")
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}
        
        # Extract netlist information
        try:
            netlist_data = extract_netlist(schematic_path)
            
            if "error" in netlist_data:
                print(f"Error extracting netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}
            
            # Advanced connection analysis
            analysis = {
                "component_count": netlist_data["component_count"],
                "net_count": netlist_data["net_count"],
                "component_types": {},
                "power_nets": [],
                "signal_nets": [],
                "potential_issues": []
            }
            
            # Analyze component types
            components = netlist_data.get("components", {})
            for ref, component in components.items():
                # Extract component type from reference (e.g., R1 -> R)
                import re
                comp_type_match = re.match(r'^([A-Za-z_]+)', ref)
                if comp_type_match:
                    comp_type = comp_type_match.group(1)
                    if comp_type not in analysis["component_types"]:
                        analysis["component_types"][comp_type] = 0
                    analysis["component_types"][comp_type] += 1
            
            # Identify power nets
            nets = netlist_data.get("nets", {})
            for net_name, pins in nets.items():
                if any(net_name.startswith(prefix) for prefix in ["VCC", "VDD", "GND", "+5V", "+3V3", "+12V"]):
                    analysis["power_nets"].append({
                        "name": net_name,
                        "pin_count": len(pins)
                    })
                else:
                    analysis["signal_nets"].append({
                        "name": net_name,
                        "pin_count": len(pins)
                    })
            
            # Check for potential issues
            # 1. Nets with only one connection (floating)
            for net_name, pins in nets.items():
                if len(pins) <= 1 and not any(net_name.startswith(prefix) for prefix in ["VCC", "VDD", "GND", "+5V", "+3V3", "+12V"]):
                    analysis["potential_issues"].append({
                        "type": "floating_net",
                        "net": net_name,
                        "description": f"Net '{net_name}' appears to be floating (only has {len(pins)} connection)"
                    })
            
            # Build result
            result = {
                "success": True,
                "schematic_path": schematic_path,
                "analysis": analysis
            }
            
            return result
            
        except Exception as e:
            print(f"Error analyzing connections: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def find_component_connections(project_path: str, component_ref: str) -> Dict[str, Any]:
        """Find all connections for a specific component in a KiCad project.
        
        This tool extracts information about how a specific component
        is connected to other components in the schematic.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            component_ref: Component reference (e.g., "R1", "U3")
            
        Returns:
            Dictionary with component connection information
        """
        print(f"Finding connections for component {component_ref} in project: {project_path}")
        
        if not os.path.exists(project_path):
            print(f"Project not found: {project_path}")
            return {"success": False, "error": f"Project not found: {project_path}"}
        
        # Get the schematic file
        try:
            files = get_project_files(project_path)
            
            if "schematic" not in files:
                print("Schematic file not found in project")
                return {"success": False, "error": "Schematic file not found in project"}
            
            schematic_path = files["schematic"]
            print(f"Found schematic file: {schematic_path}")
            
            # Extract netlist
            netlist_data = extract_netlist(schematic_path)
            
            if "error" in netlist_data:
                print(f"Failed to extract netlist: {netlist_data['error']}")
                return {"success": False, "error": netlist_data['error']}
            
            # Check if component exists in the netlist
            components = netlist_data.get("components", {})
            if component_ref not in components:
                print(f"Component {component_ref} not found in schematic")
                return {
                    "success": False, 
                    "error": f"Component {component_ref} not found in schematic",
                    "available_components": list(components.keys())
                }
            
            # Get component information
            component_info = components[component_ref]
            
            # Find connections
            nets = netlist_data.get("nets", {})
            connections = []
            connected_nets = []
            
            for net_name, pins in nets.items():
                # Check if any pin belongs to our component
                component_pins = []
                for pin in pins:
                    if pin.get('component') == component_ref:
                        component_pins.append(pin)
                        
                if component_pins:
                    # This net has connections to our component
                    net_connections = []
                    
                    for pin in component_pins:
                        pin_num = pin.get('pin', 'Unknown')
                        # Find other components connected to this pin
                        connected_components = []
                        
                        for other_pin in pins:
                            other_comp = other_pin.get('component')
                            if other_comp and other_comp != component_ref:
                                connected_components.append({
                                    "component": other_comp,
                                    "pin": other_pin.get('pin', 'Unknown')
                                })
                        
                        net_connections.append({
                            "pin": pin_num,
                            "net": net_name,
                            "connected_to": connected_components
                        })
                    
                    connections.extend(net_connections)
                    connected_nets.append(net_name)
            
            # Categorize connections by pin function (if possible)
            pin_functions = {}
            if "pins" in component_info:
                for pin in component_info["pins"]:
                    pin_num = pin.get('num')
                    pin_name = pin.get('name', '')
                    
                    # Try to categorize based on pin name
                    pin_type = "unknown"
                    
                    if any(power_term in pin_name.upper() for power_term in ["VCC", "VDD", "VEE", "VSS", "GND", "PWR", "POWER"]):
                        pin_type = "power"
                    elif any(io_term in pin_name.upper() for io_term in ["IO", "I/O", "GPIO"]):
                        pin_type = "io"
                    elif any(input_term in pin_name.upper() for input_term in ["IN", "INPUT"]):
                        pin_type = "input"
                    elif any(output_term in pin_name.upper() for output_term in ["OUT", "OUTPUT"]):
                        pin_type = "output"
                    
                    pin_functions[pin_num] = {
                        "name": pin_name,
                        "type": pin_type
                    }
            
            # Build result
            result = {
                "success": True,
                "project_path": project_path,
                "schematic_path": schematic_path,
                "component": component_ref,
                "component_info": component_info,
                "connections": connections,
                "connected_nets": connected_nets,
                "pin_functions": pin_functions,
                "total_connections": len(connections)
            }
            
            return result
            
        except Exception as e:
            print(f"Error finding component connections: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _validate_wire_connections_impl(schematic_path: str) -> Dict[str, Any]:
        """Internal implementation of wire connection validation."""
        print(f"Validating wire connections in schematic: {schematic_path}")
        
        if not os.path.exists(schematic_path):
            print(f"Schematic file not found: {schematic_path}")
            return {"success": False, "error": f"Schematic file not found: {schematic_path}"}
        
        try:
            from kicad_mcp.utils.connectivity_analyzer import analyze_connectivity
            
            print("Running coordinate-based connectivity analysis...")
            result = analyze_connectivity(schematic_path)
            
            if result.get('success'):
                summary = result.get('summary', {})
                print(f"Analysis complete: {summary.get('connected_pins', 0)} pins connected, "
                      f"{summary.get('unconnected_pins', 0)} pins unconnected "
                      f"({summary.get('connection_rate', 'N/A')})")
            else:
                print(f"Analysis failed: {result.get('error', 'Unknown error')}")
            
            return result
            
        except Exception as e:
            print(f"Error validating wire connections: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def validate_wire_connections(schematic_path: str) -> Dict[str, Any]:
        """Validate wire-to-pin connectivity using coordinate matching.
        
        This tool performs precise coordinate-based analysis to determine
        if wires are actually connected to component pins. Unlike netlist
        extraction which relies on parsed connectivity, this tool:
        
        1. Extracts pin offsets from lib_symbols definitions
        2. Calculates actual pin positions (component_position + rotated_pin_offset)
        3. Matches wire endpoints to pin positions
        
        Use this tool to verify that schematic wires physically connect
        to component pins, which can catch issues where wires appear
        connected visually but are actually misaligned.
        
        Args:
            schematic_path: Path to the KiCad schematic file (.kicad_sch)
            
        Returns:
            Dictionary with:
            - summary: Connection statistics
            - component_connections: Pin-by-pin connection status
            - unconnected_pin_list: Pins with no wire connections
            - connected_pin_list: Pins with wire connections
        """
        return _validate_wire_connections_impl(schematic_path)

    @mcp.tool()
    def validate_project_wire_connections(project_path: str) -> Dict[str, Any]:
        """Validate wire-to-pin connectivity for a KiCad project.
        
        Finds the schematic associated with a project and validates
        that wires connect to component pins using coordinate matching.
        
        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            
        Returns:
            Dictionary with wire-to-pin connectivity analysis
        """
        print(f"Validating wire connections for project: {project_path}")
        
        if not os.path.exists(project_path):
            print(f"Project not found: {project_path}")
            return {"success": False, "error": f"Project not found: {project_path}"}
        
        try:
            files = get_project_files(project_path)
            
            if "schematic" not in files:
                print("Schematic file not found in project")
                return {"success": False, "error": "Schematic file not found in project"}
            
            schematic_path = files["schematic"]
            print(f"Found schematic file: {schematic_path}")
            
            # Call the internal implementation
            result = _validate_wire_connections_impl(schematic_path)
            
            # Add project path to result
            if "success" in result and result["success"]:
                result["project_path"] = project_path
            
            return result
            
        except Exception as e:
            print(f"Error validating project wire connections: {str(e)}")
            return {"success": False, "error": str(e)}