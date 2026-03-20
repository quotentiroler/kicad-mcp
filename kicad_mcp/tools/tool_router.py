"""
Tool router for intelligent tool discovery.

Organizes all registered tools into categories so AI clients can discover
tools on demand rather than loading all schemas upfront. This reduces
context window usage by ~70% for large tool inventories.
"""
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP


# Tool category definitions with descriptions and member tool names
TOOL_CATEGORIES = {
    "analysis": {
        "description": "Schematic and PCB analysis — BOM, DRC, netlist, circuit patterns",
        "tools": [
            "analyze_bom",
            "export_bom_csv",
            "run_drc_check",
            "extract_project_netlist",
            "find_component_connections",
            "analyze_project_circuit_patterns",
            "analyze_schematic_connections",
        ],
    },
    "pcb_layout": {
        "description": "PCB component placement, optimization, and board info",
        "tools": [
            "get_pcb_info",
            "place_component",
            "place_multiple_components",
            "optimize_component_placement",
            "propose_placement_layouts",
            "apply_placement_layout",
            "suggest_placement_fixes",
            "analyze_placement_quality",
            "quick_placement_optimization",
            "auto_place_decoupling_caps",
            "suggest_component_placement",
        ],
    },
    "routing": {
        "description": "Trace routing, Freerouting integration, copper zones",
        "tools": [
            "analyze_board_for_routing",
            "get_unrouted_nets",
            "freeroute_pcb",
            "export_dsn_file",
            "import_ses_file",
            "check_freerouting_installation",
            "refill_zones",
            "add_ground_pour",
        ],
    },
    "export": {
        "description": "Manufacturing file export — Gerbers, drill, PnP, JLCPCB, thumbnails",
        "tools": [
            "export_gerbers",
            "export_drill",
            "export_pos",
            "export_jlcpcb_bom",
            "generate_pcb_thumbnail",
        ],
    },
    "firmware": {
        "description": "Embedded firmware tooling — device trees, GPIO, peripheral discovery",
        "tools": [
            "generate_device_tree",
            "extract_gpio_config",
            "extract_i2c_devices",
            "extract_spi_devices",
        ],
    },
    "project": {
        "description": "Project management — list, open, examine KiCad projects",
        "tools": [
            "list_projects",
            "get_project_details",
            "open_project",
        ],
    },
    "validation": {
        "description": "Design validation — wire checks, boundary validation",
        "tools": [
            "validate_project_wire_connections",
            "validate_wire_connections",
        ],
    },
}


def register_tool_router(mcp: FastMCP) -> None:
    """Register tool router/discovery tools with the MCP server."""

    @mcp.tool()
    def list_tool_categories() -> Dict[str, Any]:
        """List all available tool categories with descriptions.

        Use this to discover what groups of tools are available before
        requesting specific tools. This saves context by not loading
        all tool schemas at once.

        Returns:
            Dictionary mapping category names to their descriptions and tool counts
        """
        result = {}
        for cat_name, cat_info in TOOL_CATEGORIES.items():
            result[cat_name] = {
                "description": cat_info["description"],
                "tool_count": len(cat_info["tools"]),
            }
        return {
            "categories": result,
            "total_tools": sum(len(c["tools"]) for c in TOOL_CATEGORIES.values()),
        }

    @mcp.tool()
    def get_category_tools(category: str) -> Dict[str, Any]:
        """Get the list of tools in a specific category.

        Args:
            category: Category name (e.g. 'routing', 'export', 'firmware')

        Returns:
            Dictionary with tool names and the category description
        """
        cat = category.lower().strip()
        if cat not in TOOL_CATEGORIES:
            return {
                "error": f"Unknown category: {category}",
                "available": list(TOOL_CATEGORIES.keys()),
            }
        info = TOOL_CATEGORIES[cat]
        return {
            "category": cat,
            "description": info["description"],
            "tools": info["tools"],
        }

    @mcp.tool()
    def search_tools(query: str) -> Dict[str, Any]:
        """Search for tools by keyword across all categories.

        Args:
            query: Search keyword (e.g. 'gerber', 'freerouting', 'i2c')

        Returns:
            Dictionary with matching tools and their categories
        """
        q = query.lower().strip()
        matches: List[Dict[str, str]] = []

        for cat_name, cat_info in TOOL_CATEGORIES.items():
            # Search in category description
            cat_match = q in cat_info["description"].lower()
            for tool_name in cat_info["tools"]:
                if q in tool_name.lower() or cat_match:
                    matches.append({
                        "tool": tool_name,
                        "category": cat_name,
                    })

        return {
            "query": query,
            "matches": matches,
            "count": len(matches),
        }
