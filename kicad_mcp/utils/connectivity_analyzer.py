"""
KiCad schematic connectivity analyzer.

Performs coordinate-based wire-to-pin connection validation by:
1. Extracting symbol pin offsets from lib_symbols
2. Calculating actual pin positions (component_position + pin_offset)
3. Matching wire endpoints to pin positions
"""
import os
import re
import math
from typing import Any, Dict, List, Tuple, Optional
from collections import defaultdict


class ConnectivityAnalyzer:
    """Analyzes wire-to-pin connectivity in KiCad schematics using coordinate matching."""
    
    # Tolerance for coordinate matching (in mm)
    COORDINATE_TOLERANCE = 0.01
    
    def __init__(self, schematic_path: str):
        """Initialize the connectivity analyzer.
        
        Args:
            schematic_path: Path to the KiCad schematic file (.kicad_sch)
        """
        self.schematic_path = schematic_path
        self.content = ""
        
        # Parsed data
        self.lib_symbols: Dict[str, Dict] = {}  # lib_id -> {pins: {pin_num: (x, y, angle)}}
        self.components: List[Dict] = []         # Component instances
        self.wires: List[Dict] = []              # Wire segments
        self.labels: List[Dict] = []             # Labels (local + global)
        self.junctions: List[Dict] = []          # Junction points
        
        # Analysis results
        self.pin_positions: Dict[str, Tuple[float, float]] = {}  # "ref.pin" -> (x, y)
        self.wire_connections: Dict[str, List[str]] = defaultdict(list)  # "ref.pin" -> [wire_ids]
        self.unconnected_pins: List[str] = []
        self.connected_pins: List[str] = []
        
        self._load_schematic()
    
    def _load_schematic(self) -> None:
        """Load the schematic file content."""
        if not os.path.exists(self.schematic_path):
            raise FileNotFoundError(f"Schematic file not found: {self.schematic_path}")
        
        with open(self.schematic_path, 'r', encoding='utf-8') as f:
            self.content = f.read()
    
    def analyze(self) -> Dict[str, Any]:
        """Perform full connectivity analysis.
        
        Returns:
            Dictionary with connectivity analysis results
        """
        # Step 1: Parse lib_symbols to get pin offsets
        self._parse_lib_symbols()
        
        # Step 2: Parse component instances
        self._parse_components()
        
        # Step 3: Calculate actual pin positions
        self._calculate_pin_positions()
        
        # Step 4: Parse wires
        self._parse_wires()
        
        # Step 5: Parse labels and junctions
        self._parse_labels()
        self._parse_junctions()
        
        # Step 6: Match wires to pins
        self._match_wire_to_pin_connections()
        
        # Build results
        return self._build_results()
    
    def _parse_lib_symbols(self) -> None:
        """Extract pin offsets from lib_symbols section."""
        # Find lib_symbols section
        lib_start = self.content.find('(lib_symbols')
        if lib_start == -1:
            return
        
        # Find the end of lib_symbols by tracking parentheses
        depth = 0
        lib_end = lib_start
        for i, char in enumerate(self.content[lib_start:], lib_start):
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
                if depth == 0:
                    lib_end = i + 1
                    break
        
        lib_section = self.content[lib_start:lib_end]
        
        # Find top-level symbol definitions (e.g., "Device:MCP73831")
        # Pattern: (symbol "lib:name" followed by properties
        top_symbol_pattern = r'\(symbol\s+"([^"]+:?[^"]+)"'
        
        for match in re.finditer(top_symbol_pattern, lib_section):
            lib_id = match.group(1)
            
            # Skip sub-unit symbols (e.g., "MCP73831_0_1", "MCP73831_1_1")  
            # These are child symbols, we want the parent with the full lib_id
            if re.match(r'^[A-Za-z0-9_]+_\d+_\d+$', lib_id):
                continue
                
            symbol_start = match.start()
            
            # Find the symbol's extent
            depth = 0
            symbol_end = symbol_start
            for i, char in enumerate(lib_section[symbol_start:], symbol_start):
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        symbol_end = i + 1
                        break
            
            symbol_content = lib_section[symbol_start:symbol_end]
            
            # Extract pins from all sub-units within this symbol
            pins = {}
            # Pattern for pin definition: (pin type direction (at x y angle) ... (number "num" ...
            # Note: no trailing ) after number - KiCad has (effects ...) after
            pin_pattern = r'\(pin\s+\w+\s+\w+\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)\s+([\d\.-]+)\).*?\(number\s+"([^"]+)"'
            for pin_match in re.finditer(pin_pattern, symbol_content, re.DOTALL):
                x = float(pin_match.group(1))
                y = float(pin_match.group(2))
                angle = float(pin_match.group(3))
                pin_num = pin_match.group(4)
                pins[pin_num] = (x, y, angle)
            
            if pins:
                self.lib_symbols[lib_id] = {'pins': pins}
    
    def _parse_components(self) -> None:
        """Parse component instances from schematic."""
        # Find where lib_symbols ends to avoid parsing definitions
        lib_end = 0
        lib_start = self.content.find('(lib_symbols')
        if lib_start != -1:
            depth = 0
            for i, char in enumerate(self.content[lib_start:], lib_start):
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        lib_end = i + 1
                        break
        
        # Find component instances (symbols with lib_id)
        instance_content = self.content[lib_end:]
        
        # Pattern for symbol instances
        symbol_pattern = r'\(symbol\s*\n?\s*\(lib_id\s+"([^"]+)"\)'
        
        for match in re.finditer(symbol_pattern, instance_content):
            lib_id = match.group(1)
            symbol_start = match.start()
            
            # Find the symbol's full extent
            depth = 0
            symbol_end = symbol_start
            for i, char in enumerate(instance_content[symbol_start:], symbol_start):
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth == 0:
                        symbol_end = i + 1
                        break
            
            symbol_content = instance_content[symbol_start:symbol_end]
            
            # Extract position
            pos_match = re.search(r'\(at\s+([\d\.-]+)\s+([\d\.-]+)(\s+[\d\.-]+)?\)', symbol_content)
            if not pos_match:
                continue
            
            x = float(pos_match.group(1))
            y = float(pos_match.group(2))
            angle = float(pos_match.group(3).strip()) if pos_match.group(3) else 0
            
            # Extract reference
            ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', symbol_content)
            reference = ref_match.group(1) if ref_match else "Unknown"
            
            # Extract UUID
            uuid_match = re.search(r'\(uuid\s+"([^"]+)"\)', symbol_content)
            uuid = uuid_match.group(1) if uuid_match else None
            
            # Extract pin UUIDs for this instance
            instance_pins = {}
            pin_pattern = r'\(pin\s+"([^"]+)"\s+\(uuid\s+"([^"]+)"\)\)'
            for pin_match in re.finditer(pin_pattern, symbol_content):
                pin_num = pin_match.group(1)
                pin_uuid = pin_match.group(2)
                instance_pins[pin_num] = pin_uuid
            
            self.components.append({
                'lib_id': lib_id,
                'reference': reference,
                'position': {'x': x, 'y': y, 'angle': angle},
                'uuid': uuid,
                'pins': instance_pins
            })
    
    def _rotate_point(self, x: float, y: float, angle_deg: float) -> Tuple[float, float]:
        """Rotate a point around origin by angle in degrees."""
        if angle_deg == 0:
            return (x, y)
        
        angle_rad = math.radians(angle_deg)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)
    
    def _calculate_pin_positions(self) -> None:
        """Calculate actual pin positions for all component instances."""
        for component in self.components:
            lib_id = component['lib_id']
            ref = component['reference']
            comp_x = component['position']['x']
            comp_y = component['position']['y']
            comp_angle = component['position']['angle']
            
            # Get pin offsets from lib_symbol
            if lib_id not in self.lib_symbols:
                continue
            
            symbol_pins = self.lib_symbols[lib_id]['pins']
            
            for pin_num, (offset_x, offset_y, pin_angle) in symbol_pins.items():
                # Apply component rotation to pin offset
                rotated_x, rotated_y = self._rotate_point(offset_x, offset_y, comp_angle)
                
                # Calculate final position
                final_x = round(comp_x + rotated_x, 2)
                final_y = round(comp_y + rotated_y, 2)
                
                pin_key = f"{ref}.{pin_num}"
                self.pin_positions[pin_key] = (final_x, final_y)
    
    def _parse_wires(self) -> None:
        """Parse wire segments from schematic."""
        wire_pattern = r'\(wire\s+\(pts\s+\(xy\s+([\d\.-]+)\s+([\d\.-]+)\)\s+\(xy\s+([\d\.-]+)\s+([\d\.-]+)\)\)'
        
        for i, match in enumerate(re.finditer(wire_pattern, self.content)):
            self.wires.append({
                'id': f'wire_{i}',
                'start': {'x': float(match.group(1)), 'y': float(match.group(2))},
                'end': {'x': float(match.group(3)), 'y': float(match.group(4))}
            })
    
    def _parse_labels(self) -> None:
        """Parse labels from schematic."""
        # Local labels
        local_pattern = r'\(label\s+"([^"]+)"\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)'
        for match in re.finditer(local_pattern, self.content):
            self.labels.append({
                'type': 'local',
                'text': match.group(1),
                'position': {'x': float(match.group(2)), 'y': float(match.group(3))}
            })
        
        # Global labels
        global_pattern = r'\(global_label\s+"([^"]+)".*?\(at\s+([\d\.-]+)\s+([\d\.-]+)'
        for match in re.finditer(global_pattern, self.content):
            self.labels.append({
                'type': 'global',
                'text': match.group(1),
                'position': {'x': float(match.group(2)), 'y': float(match.group(3))}
            })
    
    def _parse_junctions(self) -> None:
        """Parse junction points from schematic."""
        junction_pattern = r'\(junction\s+\(at\s+([\d\.-]+)\s+([\d\.-]+)\)'
        for match in re.finditer(junction_pattern, self.content):
            self.junctions.append({
                'x': float(match.group(1)),
                'y': float(match.group(2))
            })
    
    def _coords_match(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        """Check if two coordinates match within tolerance."""
        return (abs(x1 - x2) < self.COORDINATE_TOLERANCE and 
                abs(y1 - y2) < self.COORDINATE_TOLERANCE)
    
    def _match_wire_to_pin_connections(self) -> None:
        """Match wire endpoints to pin positions."""
        for pin_key, (pin_x, pin_y) in self.pin_positions.items():
            connected = False
            
            for wire in self.wires:
                start_x, start_y = wire['start']['x'], wire['start']['y']
                end_x, end_y = wire['end']['x'], wire['end']['y']
                
                # Check if wire starts or ends at this pin
                if self._coords_match(start_x, start_y, pin_x, pin_y):
                    self.wire_connections[pin_key].append(wire['id'])
                    connected = True
                elif self._coords_match(end_x, end_y, pin_x, pin_y):
                    self.wire_connections[pin_key].append(wire['id'])
                    connected = True
            
            if connected:
                self.connected_pins.append(pin_key)
            else:
                self.unconnected_pins.append(pin_key)
    
    def _build_results(self) -> Dict[str, Any]:
        """Build the analysis results dictionary."""
        # Group connections by component
        component_connections = defaultdict(dict)
        for pin_key in self.connected_pins:
            ref, pin = pin_key.rsplit('.', 1)
            component_connections[ref][pin] = {
                'position': self.pin_positions[pin_key],
                'wire_count': len(self.wire_connections[pin_key]),
                'connected': True
            }
        
        for pin_key in self.unconnected_pins:
            ref, pin = pin_key.rsplit('.', 1)
            component_connections[ref][pin] = {
                'position': self.pin_positions[pin_key],
                'wire_count': 0,
                'connected': False
            }
        
        return {
            'success': True,
            'schematic_path': self.schematic_path,
            'summary': {
                'total_components': len(self.components),
                'total_pins_analyzed': len(self.pin_positions),
                'connected_pins': len(self.connected_pins),
                'unconnected_pins': len(self.unconnected_pins),
                'total_wires': len(self.wires),
                'total_junctions': len(self.junctions),
                'connection_rate': f"{len(self.connected_pins) / max(len(self.pin_positions), 1) * 100:.1f}%"
            },
            'component_connections': dict(component_connections),
            'unconnected_pin_list': self.unconnected_pins[:50],  # Limit to first 50
            'connected_pin_list': self.connected_pins[:50],      # Limit to first 50
        }


def analyze_connectivity(schematic_path: str) -> Dict[str, Any]:
    """Analyze wire-to-pin connectivity in a KiCad schematic.
    
    Args:
        schematic_path: Path to the KiCad schematic file (.kicad_sch)
        
    Returns:
        Dictionary with connectivity analysis results
    """
    try:
        analyzer = ConnectivityAnalyzer(schematic_path)
        return analyzer.analyze()
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
