# KiCad MCP Server (Max Health Fork)

> Fork of [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) — extended with PCB layout, auto-routing, placement optimization, manufacturing exports, and embedded firmware tooling.

**The only lightweight Python MCP server with an end-to-end PCB workflow:** analysis → placement → routing → manufacturing export.

## What's Different in This Fork

| Feature | Upstream | This Fork |
|---------|----------|-----------|
| **PCB component placement** | — | ✅ Place, move, align components |
| **Auto-routing** | — | ✅ 45° routing with clearance checking |
| **Freerouting integration** | — | ✅ Specctra DSN/SES exchange |
| **Placement optimization** | — | ✅ Simulated annealing optimizer |
| **Layout proposals** | — | ✅ Multi-layout routability scoring |
| **Gerber/drill/PnP export** | — | ✅ Full manufacturing output |
| **Zone refill** | — | ✅ Post-routing copper pour |
| **JLCPCB BOM export** | — | ✅ Assembly-ready BOM + CPL |
| **nRF52 device tree generation** | — | ✅ Zephyr .dts from schematic |
| **Tool router (token savings)** | — | ✅ Category-based discovery |
| **Python 3.9 compat** | ❌ (`X \| None`) | ✅ (`X = None`) |
| **KiCad 9.0 paths** | Partial | ✅ Versioned discovery |

## Tool Categories (~40 tools)

### Analysis & Design Review
- `analyze_bom` — BOM analysis with cost breakdown
- `run_drc_check` — Design Rule Check via kicad-cli
- `extract_project_netlist` — Netlist extraction and analysis
- `analyze_project_circuit_patterns` — Identify power supplies, filters, interfaces

### PCB Layout & Placement
- `get_pcb_info` — Board dimensions, layer count, component list
- `place_component` / `place_multiple_components` — Component placement
- `optimize_component_placement` — Simulated annealing placement optimizer
- `propose_placement_layouts` — Generate scored layout proposals
- `apply_placement_layout` — Apply a proposed layout
- `suggest_placement_fixes` — Fix overlaps and improve routability

### Routing
- `analyze_board_for_routing` — Pre-routing analysis
- `get_unrouted_nets` — List nets needing routes
- `freeroute_pcb` — Auto-route via Freerouting (DSN/SES)
- `export_dsn_file` / `import_ses_file` — Specctra file exchange
- `refill_zones` — Refill copper pours after routing

### Manufacturing Export
- `export_gerbers` — Gerber fabrication files
- `export_drill` — Excellon drill files
- `export_pos` — Pick-and-place position files
- `export_jlcpcb_bom` — JLCPCB-format BOM + CPL files
- `generate_pcb_thumbnail` — Board visualization

### Firmware / Embedded
- `generate_device_tree` — Zephyr/Linux device tree from schematic (nRF52840, STM32, ESP32)
- `extract_gpio_config` — GPIO pin configuration extraction
- `extract_i2c_devices` / `extract_spi_devices` — Peripheral discovery

### Tool Discovery (Router)
- `list_tool_categories` — Browse 7 tool categories
- `get_category_tools` — List tools in a category
- `search_tools` — Keyword search across all tools

---

## Prerequisites

- Python 3.9+
- KiCad 9.0+ (for kicad-cli)
- uv 0.8.0+ (package manager)
- [Freerouting](https://github.com/freerouting/freerouting) JAR (optional, for auto-routing)
- Any MCP-compliant client (Claude Desktop, VS Code, Cline, etc.)

## Installation

```bash
# Clone the fork
git clone https://github.com/quotentiroler/kicad-mcp.git
cd kicad-mcp

# Install dependencies
make install

# Optional: activate the environment
source .venv/bin/activate
```

## Configuration

### Environment Variables
| Variable | Description | Example |
|----------|-------------|---------|
| `KICAD_SEARCH_PATHS` | KiCad project directories | `~/pcb,~/Electronics` |
| `KICAD_USER_DIR` | KiCad user directory override | `~/Documents/KiCad` |
| `KICAD_APP_PATH` | KiCad installation path | `C:\Program Files\KiCad\9.0` |
| `FREEROUTING_JAR` | Path to freerouting JAR | `./freerouting/freerouting.jar` |

Create a `.env` file from the example: `cp .env.example .env`

### MCP Client Configuration

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
    "mcpServers": {
        "kicad": {
            "command": "/path/to/kicad-mcp/.venv/bin/python",
            "args": ["/path/to/kicad-mcp/main.py"]
        }
    }
}
```

**VS Code** (`.vscode/mcp.json`):
```json
{
    "servers": {
        "kicad": {
            "command": "python",
            "args": ["main.py"],
            "cwd": "/path/to/kicad-mcp"
        }
    }
}
```

## Example Workflows

### End-to-End PCB Manufacturing
```
1. "Analyze my Max Health project"         → BOM + DRC + netlist overview
2. "Optimize component placement"         → Simulated annealing layout
3. "Auto-route with Freerouting"          → Professional routing
4. "Refill copper zones"                  → Ground/power pours
5. "Export Gerbers and drill files"        → Fabrication files
6. "Export JLCPCB BOM and positions"       → Assembly files
```

### Firmware Bootstrapping (nRF52840)
```
1. "Extract netlist from schematic"       → Component connections
2. "Generate nRF52840 device tree"        → Zephyr .dts overlay
3. "What I2C devices are connected?"      → Peripheral discovery
```

## Upstream Compatibility

This fork stays compatible with upstream [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp). All original features (project management, analysis, DRC, BOM, netlist, pattern recognition, prompts, resources) work unchanged.

See [README_upstream.md](README_upstream.md) for the original documentation.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## License

MIT — same as upstream.
