"""
Device tree generation tools for KiCad schematics.

Generates Zephyr/Linux device tree overlays from KiCad schematic analysis.
Supports nRF52840, STM32, and ESP32 SOC families.

Extracts GPIO, I2C, SPI, and UART peripheral configurations by parsing
schematic net names and component values.
"""
import os
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from kicad_mcp.utils.file_utils import get_project_files
from kicad_mcp.utils.netlist_parser import extract_netlist


# Device tree compatible strings for common components
DEVICE_BINDINGS = {
    # Sensors
    "BMP280": "bosch,bmp280",
    "BME280": "bosch,bme280",
    "BMP388": "bosch,bmp388",
    "LSM6DSOX": "st,lsm6dso",
    "LSM6DSO": "st,lsm6dso",
    "LSM6DS3": "st,lsm6ds3",
    "MPU6050": "invensense,mpu6050",
    "MPU9250": "invensense,mpu9250",
    "HTS221": "st,hts221",
    "LPS22HB": "st,lps22hb",
    "SHT30": "sensirion,sht3x",
    "SHT40": "sensirion,sht4x",
    "MAX30102": "maxim,max30102",
    "MAX86150": "maxim,max86150",
    # PPG AFEs (SOM-specific)
    "AFE49I30": "ti,afe49i30",
    "AFE4950": "ti,afe4950",
    # Displays
    "SSD1306": "solomon,ssd1306fb",
    "ST7789": "sitronix,st7789v",
    "ILI9341": "ilitek,ili9341",
    # Memory
    "AT24C256": "atmel,at24",
    "W25Q128": "jedec,spi-nor",
    "MX25R6435F": "jedec,spi-nor",
    # Wireless / BLE (nRF52840 is the MCU itself)
    "NRF52840": "nordic,nrf52840",
    "ESP32": "espressif,esp32",
    # Power
    "BQ25180": "ti,bq25180",
    "MAX17048": "maxim,max17048",
    "MCP73831": "microchip,mcp73831",
}

# Known I2C addresses for common devices
KNOWN_I2C_ADDRESSES = {
    "BMP280": 0x76,
    "BME280": 0x76,
    "BMP388": 0x76,
    "LSM6DSOX": 0x6A,
    "LSM6DSO": 0x6A,
    "LSM6DS3": 0x6A,
    "MPU6050": 0x68,
    "MPU9250": 0x68,
    "HTS221": 0x5F,
    "LPS22HB": 0x5C,
    "SHT30": 0x44,
    "SHT40": 0x44,
    "MAX30102": 0x57,
    "MAX86150": 0x5E,
    "SSD1306": 0x3C,
    "AT24C256": 0x50,
    "MAX17048": 0x36,
    "BQ25180": 0x6A,
    "AFE49I30": 0x58,
}

# nRF52840 peripheral base addresses (Zephyr naming)
NRF52_PERIPHERALS = {
    "i2c0": {"label": "i2c0", "reg": "0x40003000"},
    "i2c1": {"label": "i2c1", "reg": "0x40004000"},
    "spi0": {"label": "spi0", "reg": "0x40003000"},
    "spi1": {"label": "spi1", "reg": "0x40004000"},
    "spi2": {"label": "spi2", "reg": "0x40023000"},
    "spi3": {"label": "spi3", "reg": "0x4002F000"},
    "uart0": {"label": "uart0", "reg": "0x40002000"},
    "uart1": {"label": "uart1", "reg": "0x40028000"},
}


def register_device_tree_tools(mcp: FastMCP) -> None:
    """Register device tree generation tools with the MCP server."""

    @mcp.tool()
    def generate_device_tree(
        project_path: str,
        target_soc: str = "nrf52840",
        output_path: str = "",
    ) -> Dict[str, Any]:
        """Generate a Zephyr/Linux device tree overlay from a KiCad schematic.

        Analyzes the schematic to discover I2C, SPI, UART, and GPIO
        peripherals and generates a .dts overlay file.

        Supported SOC families: nrf52840, stm32, esp32

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)
            target_soc: Target SOC (nrf52840, stm32, esp32)
            output_path: Optional output .dts file path

        Returns:
            Dictionary with generated device tree source and metadata
        """
        files = get_project_files(project_path)
        sch_path = files.get("schematic")
        if not sch_path or not os.path.exists(sch_path):
            return {"error": "Schematic file not found in project"}

        target_soc = target_soc.lower()
        if target_soc not in ("nrf52840", "nrf52", "stm32", "esp32"):
            return {
                "error": f"Unsupported SOC: {target_soc}",
                "supported": ["nrf52840", "stm32", "esp32"],
            }

        # Normalize nrf52 -> nrf52840
        if target_soc == "nrf52":
            target_soc = "nrf52840"

        # Extract netlist data from schematic
        netlist_data = extract_netlist(sch_path)
        if "error" in netlist_data:
            return {"error": f"Failed to parse schematic: {netlist_data['error']}"}

        components = netlist_data.get("components", [])
        nets = netlist_data.get("nets", {})

        # Discover peripherals
        i2c_devices = _discover_i2c_devices(components, nets)
        spi_devices = _discover_spi_devices(components, nets)
        uart_devices = _discover_uart_peripherals(nets)
        gpio_pins = _discover_gpio_pins(components, nets)

        # Generate DTS content
        board_name = os.path.splitext(os.path.basename(sch_path))[0]
        dts = _render_device_tree(
            target_soc, board_name, i2c_devices, spi_devices, uart_devices, gpio_pins
        )

        # Write to file if requested
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(dts)

        return {
            "success": True,
            "device_tree": dts,
            "target_soc": target_soc,
            "board_name": board_name,
            "i2c_devices": len(i2c_devices),
            "spi_devices": len(spi_devices),
            "uart_count": len(uart_devices),
            "gpio_pins": len(gpio_pins),
            "output_path": output_path or "(not saved)",
        }

    @mcp.tool()
    def extract_gpio_config(project_path: str) -> Dict[str, Any]:
        """Extract GPIO pin configuration from a KiCad schematic.

        Discovers GPIO assignments by analyzing net names connected to
        the MCU component.

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)

        Returns:
            Dictionary with GPIO pin assignments
        """
        files = get_project_files(project_path)
        sch_path = files.get("schematic")
        if not sch_path or not os.path.exists(sch_path):
            return {"error": "Schematic file not found"}

        netlist_data = extract_netlist(sch_path)
        if "error" in netlist_data:
            return {"error": f"Failed to parse schematic: {netlist_data['error']}"}

        gpio_pins = _discover_gpio_pins(
            netlist_data.get("components", []),
            netlist_data.get("nets", {}),
        )
        return {"success": True, "gpio_pins": gpio_pins}

    @mcp.tool()
    def extract_i2c_devices(project_path: str) -> Dict[str, Any]:
        """Extract I2C devices discovered in a KiCad schematic.

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)

        Returns:
            Dictionary with I2C device list and bus assignments
        """
        files = get_project_files(project_path)
        sch_path = files.get("schematic")
        if not sch_path or not os.path.exists(sch_path):
            return {"error": "Schematic file not found"}

        netlist_data = extract_netlist(sch_path)
        if "error" in netlist_data:
            return {"error": f"Failed to parse schematic: {netlist_data['error']}"}

        devices = _discover_i2c_devices(
            netlist_data.get("components", []),
            netlist_data.get("nets", {}),
        )
        return {"success": True, "i2c_devices": devices}

    @mcp.tool()
    def extract_spi_devices(project_path: str) -> Dict[str, Any]:
        """Extract SPI devices discovered in a KiCad schematic.

        Args:
            project_path: Path to the KiCad project file (.kicad_pro)

        Returns:
            Dictionary with SPI device list and bus assignments
        """
        files = get_project_files(project_path)
        sch_path = files.get("schematic")
        if not sch_path or not os.path.exists(sch_path):
            return {"error": "Schematic file not found"}

        netlist_data = extract_netlist(sch_path)
        if "error" in netlist_data:
            return {"error": f"Failed to parse schematic: {netlist_data['error']}"}

        devices = _discover_spi_devices(
            netlist_data.get("components", []),
            netlist_data.get("nets", {}),
        )
        return {"success": True, "spi_devices": devices}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_binding(value: str) -> Optional[str]:
    """Find device tree compatible string for a component value."""
    if not value:
        return None
    val_upper = value.upper().replace("-", "").replace("_", "")
    for key, compat in DEVICE_BINDINGS.items():
        if key.upper().replace("-", "").replace("_", "") in val_upper:
            return compat
    return None


def _find_i2c_address(value: str) -> Optional[int]:
    """Look up default I2C address for a known component."""
    if not value:
        return None
    val_upper = value.upper().replace("-", "").replace("_", "")
    for key, addr in KNOWN_I2C_ADDRESSES.items():
        if key.upper().replace("-", "").replace("_", "") in val_upper:
            return addr
    return None


def _infer_bus_type(net_name: str) -> Optional[str]:
    """Infer peripheral bus type from net name."""
    if not net_name:
        return None
    n = net_name.upper()
    if any(kw in n for kw in ("I2C", "TWI", "SDA", "SCL")):
        return "I2C"
    if any(kw in n for kw in ("SPI", "MOSI", "MISO", "SCLK", "SCK", "CS_")):
        return "SPI"
    if any(kw in n for kw in ("UART", "USART", "TX", "RX", "SERIAL")):
        return "UART"
    return None


def _infer_bus_number(net_name: str) -> int:
    """Try to extract bus instance number from net name (e.g. I2C1 -> 1)."""
    m = re.search(r"[_\s]?(\d)$", net_name)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:I2C|SPI|UART|TWI|USART)(\d)", net_name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 0


def _discover_i2c_devices(
    components: List[Dict], nets: Dict
) -> List[Dict[str, Any]]:
    """Discover I2C devices from schematic netlist data."""
    devices = []
    seen_refs = set()

    for comp in components:
        ref = comp.get("reference", "")
        value = comp.get("value", "")
        if ref in seen_refs:
            continue

        binding = _find_binding(value)
        addr = _find_i2c_address(value)
        if not addr:
            continue

        # Check if any net connected to this component looks like I2C
        is_i2c = False
        bus_num = 0
        for net_name, net_info in nets.items():
            components_on_net = net_info if isinstance(net_info, list) else net_info.get("components", [])
            refs_on_net = [c.get("ref", c.get("reference", "")) for c in components_on_net] if isinstance(components_on_net, list) else []

            if ref in refs_on_net and _infer_bus_type(net_name) == "I2C":
                is_i2c = True
                bus_num = _infer_bus_number(net_name)
                break

        if is_i2c or binding:
            seen_refs.add(ref)
            devices.append({
                "reference": ref,
                "value": value,
                "compatible": binding or f"vendor,{value.lower()}",
                "address": addr,
                "bus": bus_num,
            })

    return devices


def _discover_spi_devices(
    components: List[Dict], nets: Dict
) -> List[Dict[str, Any]]:
    """Discover SPI devices from schematic netlist data."""
    devices = []
    seen_refs = set()

    for comp in components:
        ref = comp.get("reference", "")
        value = comp.get("value", "")
        if ref in seen_refs:
            continue

        binding = _find_binding(value)
        is_spi = False
        bus_num = 0
        cs_index = 0

        for net_name, net_info in nets.items():
            components_on_net = net_info if isinstance(net_info, list) else net_info.get("components", [])
            refs_on_net = [c.get("ref", c.get("reference", "")) for c in components_on_net] if isinstance(components_on_net, list) else []

            if ref in refs_on_net and _infer_bus_type(net_name) == "SPI":
                is_spi = True
                bus_num = _infer_bus_number(net_name)
                break

        if is_spi and binding:
            seen_refs.add(ref)
            devices.append({
                "reference": ref,
                "value": value,
                "compatible": binding,
                "bus": bus_num,
                "cs": cs_index,
                "frequency": 8000000,
            })

    return devices


def _discover_uart_peripherals(nets: Dict) -> List[Dict[str, Any]]:
    """Discover UART peripherals from net names."""
    uart_buses = set()
    for net_name in nets:
        if _infer_bus_type(net_name) == "UART":
            bus_num = _infer_bus_number(net_name)
            uart_buses.add(bus_num)
    return [{"bus": n} for n in sorted(uart_buses)]


def _discover_gpio_pins(
    components: List[Dict], nets: Dict
) -> List[Dict[str, Any]]:
    """Discover GPIO pin assignments from net names."""
    gpio_pins = []
    gpio_pattern = re.compile(r"P(\d+)\.(\d+)|GPIO(\d+)", re.IGNORECASE)

    for net_name in nets:
        m = gpio_pattern.search(net_name)
        if m:
            if m.group(1) is not None:
                port = int(m.group(1))
                pin = int(m.group(2))
                gpio_pins.append({
                    "net": net_name,
                    "port": port,
                    "pin": pin,
                    "label": net_name,
                })
            elif m.group(3) is not None:
                gpio_num = int(m.group(3))
                gpio_pins.append({
                    "net": net_name,
                    "port": gpio_num // 32,
                    "pin": gpio_num % 32,
                    "label": net_name,
                })

    return gpio_pins


def _render_device_tree(
    soc: str,
    board_name: str,
    i2c_devices: List[Dict],
    spi_devices: List[Dict],
    uart_devices: List[Dict],
    gpio_pins: List[Dict],
) -> str:
    """Render device tree overlay source."""
    lines = [
        f"/* Device Tree Overlay for {board_name} */",
        f"/* Target SOC: {soc} */",
        "/* Auto-generated from KiCad schematic — review before use */",
        "",
    ]

    if soc == "nrf52840":
        lines.append("/ {")
        lines.append(f'\tmodel = "{board_name}";')
        lines.append(f'\tcompatible = "nordic,nrf52840-dk-nrf52840";')
        lines.append("};")
        lines.append("")
    elif soc == "stm32":
        lines.append("/ {")
        lines.append(f'\tmodel = "{board_name}";')
        lines.append(f'\tcompatible = "st,stm32";')
        lines.append("};")
        lines.append("")
    elif soc == "esp32":
        lines.append("/ {")
        lines.append(f'\tmodel = "{board_name}";')
        lines.append(f'\tcompatible = "espressif,esp32";')
        lines.append("};")
        lines.append("")

    # I2C buses
    i2c_by_bus: Dict[int, List[Dict]] = {}
    for dev in i2c_devices:
        i2c_by_bus.setdefault(dev["bus"], []).append(dev)

    for bus_num, devs in sorted(i2c_by_bus.items()):
        bus_label = f"i2c{bus_num}"
        lines.append(f"&{bus_label} {{")
        lines.append("\tstatus = \"okay\";")
        lines.append("")

        for dev in devs:
            addr_hex = f"0x{dev['address']:02x}"
            ref_lower = dev["reference"].lower()
            lines.append(f"\t{ref_lower}: {dev['compatible'].split(',')[-1]}@{dev['address']:02x} {{")
            lines.append(f'\t\tcompatible = "{dev["compatible"]}";')
            lines.append(f"\t\treg = <{addr_hex}>;")
            lines.append(f'\t\tlabel = "{dev["value"]}";')
            lines.append("\t};")
            lines.append("")

        lines.append("};")
        lines.append("")

    # SPI buses
    spi_by_bus: Dict[int, List[Dict]] = {}
    for dev in spi_devices:
        spi_by_bus.setdefault(dev["bus"], []).append(dev)

    for bus_num, devs in sorted(spi_by_bus.items()):
        bus_label = f"spi{bus_num}"
        lines.append(f"&{bus_label} {{")
        lines.append("\tstatus = \"okay\";")
        lines.append("")

        for dev in devs:
            ref_lower = dev["reference"].lower()
            lines.append(f"\t{ref_lower}: {dev['compatible'].split(',')[-1]}@{dev['cs']} {{")
            lines.append(f'\t\tcompatible = "{dev["compatible"]}";')
            lines.append(f"\t\treg = <{dev['cs']}>;")
            lines.append(f"\t\tspi-max-frequency = <{dev['frequency']}>;")
            lines.append(f'\t\tlabel = "{dev["value"]}";')
            lines.append("\t};")
            lines.append("")

        lines.append("};")
        lines.append("")

    # UART
    for uart in uart_devices:
        bus_label = f"uart{uart['bus']}"
        lines.append(f"&{bus_label} {{")
        lines.append("\tstatus = \"okay\";")
        lines.append("\tcurrent-speed = <115200>;")
        lines.append("};")
        lines.append("")

    # GPIO aliases
    if gpio_pins:
        lines.append("/ {")
        lines.append("\tgpio_keys {")
        lines.append('\t\tcompatible = "gpio-keys";')
        for gp in gpio_pins:
            safe_label = re.sub(r"[^a-zA-Z0-9_]", "_", gp["label"]).lower()
            lines.append(f"\t\t{safe_label} {{")
            lines.append(f'\t\t\tlabel = "{gp["label"]}";')
            lines.append(f"\t\t\tgpios = <&gpio{gp['port']} {gp['pin']} 0>;")
            lines.append("\t\t};")
        lines.append("\t};")
        lines.append("};")
        lines.append("")

    return "\n".join(lines)
