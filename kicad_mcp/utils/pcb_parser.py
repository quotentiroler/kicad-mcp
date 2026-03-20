"""
PCB file parser and writer for KiCad .kicad_pcb files.

Handles S-expression format for reading and modifying PCB layouts.
"""

import os
import re
import logging
from typing import Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Footprint:
    """Represents a footprint (component) on the PCB."""
    reference: str
    footprint_lib: str  # e.g., "Package_QFN:QFN-48-1EP_7x7mm_P0.5mm"
    position: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    layer: str = "F.Cu"
    locked: bool = False
    uuid: str = ""
    raw_sexpr: str = ""  # Original S-expression for preservation


@dataclass
class Track:
    """Represents a copper track (trace) on the PCB."""
    start: tuple[float, float]
    end: tuple[float, float]
    width: float = 0.25
    layer: str = "F.Cu"
    net: int = 0
    uuid: str = ""


@dataclass
class Via:
    """Represents a via on the PCB."""
    position: tuple[float, float]
    size: float = 0.8
    drill: float = 0.4
    layers: tuple[str, str] = ("F.Cu", "B.Cu")
    net: int = 0
    uuid: str = ""


@dataclass
class PCBData:
    """Parsed PCB file data."""
    version: str = ""
    board_thickness: float = 1.6
    board_outline: list[tuple[float, float]] = field(default_factory=list)
    layers: list[dict] = field(default_factory=list)
    nets: dict[int, str] = field(default_factory=dict)  # net_id -> net_name
    footprints: list[Footprint] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    raw_content: str = ""  # Full file content for modification


def parse_sexpr(text: str) -> list:
    """Parse S-expression into nested Python lists."""
    result = []
    stack = [result]
    current_token = ""
    in_string = False
    
    i = 0
    while i < len(text):
        char = text[i]
        
        if char == '"' and (i == 0 or text[i-1] != '\\'):
            in_string = not in_string
            current_token += char
        elif in_string:
            current_token += char
        elif char == '(':
            if current_token.strip():
                stack[-1].append(current_token.strip())
                current_token = ""
            new_list = []
            stack[-1].append(new_list)
            stack.append(new_list)
        elif char == ')':
            if current_token.strip():
                stack[-1].append(current_token.strip())
                current_token = ""
            if len(stack) > 1:
                stack.pop()
        elif char in ' \t\n\r':
            if current_token.strip():
                stack[-1].append(current_token.strip())
                current_token = ""
        else:
            current_token += char
        i += 1
    
    return result[0] if result else []


def sexpr_to_string(sexpr: list, indent: int = 0) -> str:
    """Convert nested Python list back to S-expression string."""
    if not isinstance(sexpr, list):
        return str(sexpr)
    
    if not sexpr:
        return "()"
    
    # Check if this is a simple list (no nested lists)
    has_nested = any(isinstance(item, list) for item in sexpr)
    
    if not has_nested and len(sexpr) <= 4:
        # Simple single-line format
        items = ' '.join(str(item) for item in sexpr)
        return f"({items})"
    
    # Multi-line format
    lines = ["("]
    for item in sexpr:
        if isinstance(item, list):
            lines.append('\t' * (indent + 1) + sexpr_to_string(item, indent + 1))
        else:
            if sexpr.index(item) == 0:
                lines[0] += str(item)
            else:
                lines.append('\t' * (indent + 1) + str(item))
    lines.append('\t' * indent + ')')
    return '\n'.join(lines)


def parse_pcb_file(pcb_path: str) -> PCBData:
    """Parse a KiCad PCB file and extract key data.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        
    Returns:
        PCBData object with parsed information
    """
    if not os.path.exists(pcb_path):
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")
    
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    pcb = PCBData(raw_content=content)
    
    # Parse version
    version_match = re.search(r'\(version\s+(\d+)\)', content)
    if version_match:
        pcb.version = version_match.group(1)
    
    # Parse nets
    for match in re.finditer(r'\(net\s+(\d+)\s+"([^"]*)"\)', content):
        net_id = int(match.group(1))
        net_name = match.group(2)
        pcb.nets[net_id] = net_name
    
    # Parse board outline from Edge.Cuts layer
    # Look for gr_rect or gr_poly on Edge.Cuts
    # Pattern for KiCad 9.x format with nested structure
    outline_match = re.search(
        r'\(gr_rect\s*\(start\s+([\d.-]+)\s+([\d.-]+)\)\s*\(end\s+([\d.-]+)\s+([\d.-]+)\)',
        content, re.DOTALL
    )
    if outline_match:
        x1, y1 = float(outline_match.group(1)), float(outline_match.group(2))
        x2, y2 = float(outline_match.group(3)), float(outline_match.group(4))
        pcb.board_outline = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    
    # Parse footprints
    footprint_pattern = re.compile(
        r'\(footprint\s+"([^"]+)"[^(]*'
        r'(?:\(layer\s+"([^"]+)"\))?[^(]*'
        r'(?:\(uuid\s+"([^"]+)"\))?[^(]*'
        r'(?:\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\))?',
        re.DOTALL
    )
    
    # More robust footprint parsing
    fp_starts = [m.start() for m in re.finditer(r'\(footprint\s+"', content)]
    
    for start in fp_starts:
        # Find matching closing paren
        depth = 0
        end = start
        for i in range(start, len(content)):
            if content[i] == '(':
                depth += 1
            elif content[i] == ')':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        
        fp_text = content[start:end]
        
        # Extract footprint library
        lib_match = re.search(r'\(footprint\s+"([^"]+)"', fp_text)
        lib_name = lib_match.group(1) if lib_match else ""
        
        # Extract reference - KiCad 9 uses property "Reference", older uses fp_text reference
        ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_text)
        if not ref_match:
            ref_match = re.search(r'\(fp_text\s+reference\s+"([^"]+)"', fp_text)
        reference = ref_match.group(1) if ref_match else ""
        
        # Extract position
        at_match = re.search(r'\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)', fp_text)
        if at_match:
            x, y = float(at_match.group(1)), float(at_match.group(2))
            rot = float(at_match.group(3)) if at_match.group(3) else 0.0
        else:
            x, y, rot = 0.0, 0.0, 0.0
        
        # Extract layer
        layer_match = re.search(r'\(layer\s+"([^"]+)"\)', fp_text)
        layer = layer_match.group(1) if layer_match else "F.Cu"
        
        # Extract UUID
        uuid_match = re.search(r'\(uuid\s+"([^"]+)"\)', fp_text)
        uuid = uuid_match.group(1) if uuid_match else ""
        
        if reference:  # Only add if we found a reference
            pcb.footprints.append(Footprint(
                reference=reference,
                footprint_lib=lib_name,
                position=(x, y),
                rotation=rot,
                layer=layer,
                uuid=uuid,
                raw_sexpr=fp_text
            ))
    
    # Parse tracks
    for match in re.finditer(
        r'\(segment\s+\(start\s+([\d.-]+)\s+([\d.-]+)\)\s*'
        r'\(end\s+([\d.-]+)\s+([\d.-]+)\)\s*'
        r'\(width\s+([\d.-]+)\)\s*'
        r'\(layer\s+"([^"]+)"\)\s*'
        r'\(net\s+(\d+)\)',
        content
    ):
        pcb.tracks.append(Track(
            start=(float(match.group(1)), float(match.group(2))),
            end=(float(match.group(3)), float(match.group(4))),
            width=float(match.group(5)),
            layer=match.group(6),
            net=int(match.group(7))
        ))
    
    # Parse vias
    for match in re.finditer(
        r'\(via\s+\(at\s+([\d.-]+)\s+([\d.-]+)\)\s*'
        r'\(size\s+([\d.-]+)\)\s*'
        r'\(drill\s+([\d.-]+)\)\s*'
        r'\(layers\s+"([^"]+)"\s+"([^"]+)"\)\s*'
        r'\(net\s+(\d+)\)',
        content
    ):
        pcb.vias.append(Via(
            position=(float(match.group(1)), float(match.group(2))),
            size=float(match.group(3)),
            drill=float(match.group(4)),
            layers=(match.group(5), match.group(6)),
            net=int(match.group(7))
        ))
    
    logger.info(f"Parsed PCB: {len(pcb.footprints)} footprints, {len(pcb.tracks)} tracks, {len(pcb.vias)} vias")
    return pcb


def generate_uuid() -> str:
    """Generate a KiCad-style UUID."""
    import uuid
    return str(uuid.uuid4())


def create_footprint_sexpr(
    reference: str,
    footprint_lib: str,
    position: tuple[float, float],
    rotation: float = 0.0,
    layer: str = "F.Cu"
) -> str:
    """Generate S-expression for a footprint placement.
    
    Note: This creates a minimal footprint reference. The actual footprint
    definition comes from the library. KiCad's "Update PCB from Schematic"
    handles this automatically.
    """
    x, y = position
    uuid = generate_uuid()
    
    rot_str = f" {rotation}" if rotation != 0 else ""
    
    return f'''(footprint "{footprint_lib}"
		(layer "{layer}")
		(uuid "{uuid}")
		(at {x} {y}{rot_str})
		(fp_text reference "{reference}" (at 0 -2) (layer "{layer}")
			(effects (font (size 1 1) (thickness 0.15)))
		)
		(fp_text value "{footprint_lib.split(':')[-1]}" (at 0 2) (layer "{layer}")
			(effects (font (size 1 1) (thickness 0.15)))
		)
	)'''


def create_track_sexpr(
    start: tuple[float, float],
    end: tuple[float, float],
    width: float = 0.25,
    layer: str = "F.Cu",
    net: int = 0
) -> str:
    """Generate S-expression for a track segment."""
    uuid = generate_uuid()
    return f'(segment (start {start[0]} {start[1]}) (end {end[0]} {end[1]}) (width {width}) (layer "{layer}") (net {net}) (uuid "{uuid}"))'


def create_via_sexpr(
    position: tuple[float, float],
    size: float = 0.8,
    drill: float = 0.4,
    layers: tuple[str, str] = ("F.Cu", "B.Cu"),
    net: int = 0
) -> str:
    """Generate S-expression for a via."""
    uuid = generate_uuid()
    return f'(via (at {position[0]} {position[1]}) (size {size}) (drill {drill}) (layers "{layers[0]}" "{layers[1]}") (net {net}) (uuid "{uuid}"))'


def add_footprint_to_pcb(pcb_path: str, footprint: Footprint) -> bool:
    """Add a footprint to an existing PCB file.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        footprint: Footprint object to add
        
    Returns:
        True if successful
    """
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find insertion point (before the final closing paren)
    # Look for last ) that closes the kicad_pcb
    insert_pos = content.rfind(')')
    
    if insert_pos == -1:
        raise ValueError("Invalid PCB file format")
    
    fp_sexpr = create_footprint_sexpr(
        reference=footprint.reference,
        footprint_lib=footprint.footprint_lib,
        position=footprint.position,
        rotation=footprint.rotation,
        layer=footprint.layer
    )
    
    new_content = content[:insert_pos] + '\n\t' + fp_sexpr + '\n' + content[insert_pos:]
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    logger.info(f"Added footprint {footprint.reference} at {footprint.position}")
    return True


def update_footprint_position(
    pcb_path: str,
    reference: str,
    new_position: tuple[float, float],
    new_rotation: float | None = None
) -> bool:
    """Update the position of an existing footprint.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        reference: Component reference (e.g., "U1", "C1")
        new_position: New (x, y) position
        new_rotation: New rotation in degrees (optional)
        
    Returns:
        True if successful
    """
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find all footprint blocks by looking for (footprint followed by (property "Reference" "XXX"
    # We need to handle multi-line matching
    
    # First find where this reference appears
    ref_pattern = rf'\(property\s+"Reference"\s+"{re.escape(reference)}"'
    ref_match = re.search(ref_pattern, content)
    
    if not ref_match:
        # Try older format
        ref_pattern = rf'\(fp_text\s+reference\s+"{re.escape(reference)}"'
        ref_match = re.search(ref_pattern, content)
    
    if not ref_match:
        logger.warning(f"Footprint {reference} not found in PCB")
        return False
    
    # Find the enclosing (footprint ...) block by searching backwards
    # Look for the last (footprint before this reference
    search_start = max(0, ref_match.start() - 10000)  # Look back up to 10000 chars for large footprints
    search_region = content[search_start:ref_match.start()]
    
    # Find the last occurrence of "(footprint" in the search region
    fp_matches = list(re.finditer(r'\(footprint\s+"[^"]+"', search_region))
    if not fp_matches:
        logger.warning(f"Could not find footprint block for {reference}")
        return False
    
    fp_start = search_start + fp_matches[-1].start()
    
    # The footprint's main (at x y) comes right after (footprint "name"), (layer), and (uuid)
    # It should be within the first ~200 chars, BEFORE the (property "Reference" line
    # We need to find ONLY the top-level (at), not nested ones in pads or text
    
    # Strategy: Find the (at) that appears after (uuid) but before (property or (pad or (fp_text
    fp_header = content[fp_start:fp_start + 300]
    
    # Look for the uuid line first, then find (at after it
    uuid_match = re.search(r'\(uuid\s+"[^"]+"\)', fp_header)
    if uuid_match:
        # Search for (at) right after uuid
        after_uuid = fp_header[uuid_match.end():]
        # The main (at) should be on the next line, with proper indentation (tab + tab or similar)
        at_pattern = re.compile(r'^\s*\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)', re.MULTILINE)
        at_match = at_pattern.search(after_uuid)
        
        if at_match:
            at_abs_start = fp_start + uuid_match.end() + at_match.start()
            at_abs_end = fp_start + uuid_match.end() + at_match.end()
        else:
            # Fallback: find first (at) in the header section
            at_pattern = re.compile(r'\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)')
            at_match = at_pattern.search(fp_header)
            if not at_match:
                logger.warning(f"Position not found for footprint {reference}")
                return False
            at_abs_start = fp_start + at_match.start()
            at_abs_end = fp_start + at_match.end()
    else:
        # No uuid found, use original fallback - find first (at) in the footprint header
        at_pattern = re.compile(r'\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)')
        at_match = at_pattern.search(fp_header)
        if not at_match:
            logger.warning(f"Position not found for footprint {reference}")
            return False
        at_abs_start = fp_start + at_match.start()
        at_abs_end = fp_start + at_match.end()
    
    # Build replacement
    x, y = new_position
    if new_rotation is not None:
        new_at = f"(at {x} {y} {new_rotation})"
    else:
        # Preserve existing rotation if not specified
        old_rot = at_match.group(3) if at_match.lastindex and at_match.lastindex >= 3 else None
        if old_rot:
            new_at = f"(at {x} {y} {old_rot})"
        else:
            new_at = f"(at {x} {y})"
    
    new_content = content[:at_abs_start] + new_at + content[at_abs_end:]
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    logger.info(f"Updated {reference} position to {new_position}")
    return True


def add_track_to_pcb(pcb_path: str, track: Track) -> bool:
    """Add a track segment to an existing PCB file."""
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    insert_pos = content.rfind(')')
    
    track_sexpr = create_track_sexpr(
        start=track.start,
        end=track.end,
        width=track.width,
        layer=track.layer,
        net=track.net
    )
    
    new_content = content[:insert_pos] + '\n\t' + track_sexpr + '\n' + content[insert_pos:]
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    logger.info(f"Added track from {track.start} to {track.end} on {track.layer}")
    return True


def add_via_to_pcb(pcb_path: str, via: Via) -> bool:
    """Add a via to an existing PCB file."""
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    insert_pos = content.rfind(')')
    
    via_sexpr = create_via_sexpr(
        position=via.position,
        size=via.size,
        drill=via.drill,
        layers=via.layers,
        net=via.net
    )
    
    new_content = content[:insert_pos] + '\n\t' + via_sexpr + '\n' + content[insert_pos:]
    
    with open(pcb_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    logger.info(f"Added via at {via.position}")
    return True


def get_net_id_by_name(pcb: PCBData, net_name: str) -> int | None:
    """Find net ID by name."""
    for net_id, name in pcb.nets.items():
        if name == net_name:
            return net_id
    return None


def get_board_bounds(pcb: PCBData) -> tuple[float, float, float, float]:
    """Get board bounding box (min_x, min_y, max_x, max_y)."""
    if pcb.board_outline:
        xs = [p[0] for p in pcb.board_outline]
        ys = [p[1] for p in pcb.board_outline]
        return (min(xs), min(ys), max(xs), max(ys))
    return (0, 0, 35, 25)  # Default SOM Band size


def find_kicad_footprint_libraries() -> list[str]:
    """Find KiCad footprint library paths on the system."""
    import platform
    
    paths = []
    system = platform.system()
    
    if system == "Windows":
        # Standard KiCad installation paths on Windows
        kicad_paths = [
            r"C:\Program Files\KiCad\9.0\share\kicad\footprints",
            r"C:\Program Files\KiCad\8.0\share\kicad\footprints",
            r"C:\Program Files\KiCad\7.0\share\kicad\footprints",
            r"C:\Program Files\KiCad\share\kicad\footprints",
            os.path.expanduser(r"~\Documents\KiCad\9.0\footprints"),
            os.path.expanduser(r"~\Documents\KiCad\8.0\footprints"),
            os.path.expanduser(r"~\Documents\KiCad\7.0\footprints"),
        ]
    elif system == "Darwin":  # macOS
        kicad_paths = [
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
            os.path.expanduser("~/Documents/KiCad/footprints"),
        ]
    else:  # Linux
        kicad_paths = [
            "/usr/share/kicad/footprints",
            "/usr/local/share/kicad/footprints",
            os.path.expanduser("~/.local/share/kicad/footprints"),
        ]
    
    for path in kicad_paths:
        if os.path.isdir(path):
            paths.append(path)
    
    return paths


def find_footprint_file(footprint_lib: str) -> str | None:
    """Find a footprint file given a library:footprint string.
    
    Args:
        footprint_lib: Footprint identifier like "Package_QFN:QFN-48-1EP_7x7mm_P0.5mm"
        
    Returns:
        Path to .kicad_mod file or None if not found
    """
    if ':' not in footprint_lib:
        return None
    
    lib_name, fp_name = footprint_lib.split(':', 1)
    
    lib_paths = find_kicad_footprint_libraries()
    
    for base_path in lib_paths:
        # Look for library folder (e.g., Package_QFN.pretty)
        lib_folder = os.path.join(base_path, f"{lib_name}.pretty")
        if os.path.isdir(lib_folder):
            # Look for footprint file
            fp_file = os.path.join(lib_folder, f"{fp_name}.kicad_mod")
            if os.path.isfile(fp_file):
                return fp_file
    
    return None


def read_footprint_from_library(footprint_lib: str) -> str | None:
    """Read a footprint definition from KiCad libraries.
    
    Args:
        footprint_lib: Footprint identifier like "Package_QFN:QFN-48-1EP_7x7mm_P0.5mm"
        
    Returns:
        Footprint S-expression content or None if not found
    """
    fp_file = find_footprint_file(footprint_lib)
    
    if fp_file is None:
        logger.warning(f"Footprint not found in libraries: {footprint_lib}")
        return None
    
    try:
        with open(fp_file, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        logger.error(f"Error reading footprint file {fp_file}: {e}")
        return None


def create_full_footprint_sexpr(
    reference: str,
    footprint_lib: str,
    position: tuple[float, float],
    rotation: float = 0.0,
    layer: str = "F.Cu"
) -> str | None:
    """Create a complete footprint S-expression by reading from library.
    
    This reads the actual footprint definition from KiCad libraries
    and modifies it with the correct reference, position, and layer.
    
    Args:
        reference: Component reference (e.g., "U1", "C1")
        footprint_lib: Library:footprint identifier
        position: (x, y) position on PCB
        rotation: Rotation in degrees
        layer: Target layer ("F.Cu" or "B.Cu")
        
    Returns:
        Complete footprint S-expression or None if library not found
    """
    # Try to read from library first
    lib_content = read_footprint_from_library(footprint_lib)
    
    if lib_content is None:
        # Fall back to minimal footprint
        logger.info(f"Using minimal footprint for {reference} (library not found)")
        return create_footprint_sexpr(reference, footprint_lib, position, rotation, layer)
    
    uuid = generate_uuid()
    x, y = position
    rot_str = f" {rotation}" if rotation != 0 else ""
    
    # Modify the library footprint with our specifics
    # Replace the footprint header
    modified = re.sub(
        r'\(footprint\s+"[^"]*"',
        f'(footprint "{footprint_lib}"',
        lib_content,
        count=1
    )
    
    # Update or add layer
    if '(layer' in modified:
        modified = re.sub(
            r'\(layer\s+"[^"]+"\)',
            f'(layer "{layer}")',
            modified,
            count=1
        )
    else:
        # Add layer after footprint name
        modified = re.sub(
            r'(\(footprint\s+"[^"]+")',
            f'\\1\n\t\t(layer "{layer}")',
            modified,
            count=1
        )
    
    # Update or add UUID
    if '(uuid' in modified:
        modified = re.sub(
            r'\(uuid\s+"[^"]+"\)',
            f'(uuid "{uuid}")',
            modified,
            count=1
        )
    else:
        modified = re.sub(
            r'(\(layer\s+"[^"]+"\))',
            f'\\1\n\t\t(uuid "{uuid}")',
            modified,
            count=1
        )
    
    # Update or add position
    if '(at ' in modified:
        modified = re.sub(
            r'\(at\s+[\d.-]+\s+[\d.-]+(?:\s+[\d.-]+)?\)',
            f'(at {x} {y}{rot_str})',
            modified,
            count=1
        )
    else:
        modified = re.sub(
            r'(\(uuid\s+"[^"]+"\))',
            f'\\1\n\t\t(at {x} {y}{rot_str})',
            modified,
            count=1
        )
    
    # Update reference text
    modified = re.sub(
        r'\(fp_text\s+reference\s+"[^"]*"',
        f'(fp_text reference "{reference}"',
        modified,
        count=1
    )
    
    return modified


def import_footprints_from_schematic(
    pcb_path: str,
    schematic_components: list[dict],
    board_width: float = 35.0,
    board_height: float = 25.0
) -> dict:
    """Import footprints from schematic components into PCB.
    
    Args:
        pcb_path: Path to .kicad_pcb file
        schematic_components: List of component dicts with 'reference', 'footprint' keys
        board_width: Board width for initial placement grid
        board_height: Board height for initial placement grid
        
    Returns:
        Dict with import results
    """
    with open(pcb_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Parse existing footprints to avoid duplicates
    existing_refs = set()
    for match in re.finditer(r'\(fp_text\s+reference\s+"([^"]+)"', content):
        existing_refs.add(match.group(1))
    
    # Calculate grid positions for new components
    grid_cols = max(1, int(board_width / 5))  # 5mm spacing
    
    added = []
    skipped = []
    failed = []
    
    insert_pos = content.rfind(')')
    new_footprints = []
    
    for i, comp in enumerate(schematic_components):
        ref = comp.get('reference', '')
        footprint = comp.get('footprint', '')
        
        if not ref or not footprint:
            failed.append({"reference": ref, "reason": "Missing reference or footprint"})
            continue
        
        if ref in existing_refs:
            skipped.append({"reference": ref, "reason": "Already exists in PCB"})
            continue
        
        # Calculate grid position (components start outside board, user arranges)
        row = i // grid_cols
        col = i % grid_cols
        x = board_width + 10 + (col * 5)  # Place to right of board
        y = 5 + (row * 5)
        
        # Create footprint
        fp_sexpr = create_full_footprint_sexpr(
            reference=ref,
            footprint_lib=footprint,
            position=(x, y),
            rotation=0.0,
            layer="F.Cu"
        )
        
        if fp_sexpr:
            new_footprints.append(fp_sexpr)
            added.append({
                "reference": ref,
                "footprint": footprint,
                "position": (x, y)
            })
        else:
            failed.append({"reference": ref, "reason": "Could not create footprint"})
    
    # Add all new footprints to PCB
    if new_footprints:
        all_footprints = '\n\n\t'.join(new_footprints)
        new_content = content[:insert_pos] + '\n\n\t' + all_footprints + '\n' + content[insert_pos:]
        
        with open(pcb_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    
    return {
        "added": len(added),
        "skipped": len(skipped),
        "failed": len(failed),
        "details": {
            "added": added,
            "skipped": skipped,
            "failed": failed
        }
    }
