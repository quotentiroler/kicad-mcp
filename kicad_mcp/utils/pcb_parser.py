"""
PCB file parser and writer for KiCad .kicad_pcb files.

Handles S-expression format for reading and modifying PCB layouts.
"""

from dataclasses import dataclass, field
import logging
import os
import re

from .sexpr import form_span, to_float

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


def parse_pcb_file(pcb_path: str) -> PCBData:
    """Parse a KiCad PCB file and extract key data.

    Args:
        pcb_path: Path to .kicad_pcb file

    Returns:
        PCBData object with parsed information
    """
    if not os.path.exists(pcb_path):
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    with open(pcb_path, encoding="utf-8") as f:
        content = f.read()

    open_paren = content.find("(")
    if open_paren == -1:
        raise ValueError(f"Not a KiCad PCB file, no S-expression content: {pcb_path}")
    form_span(content, open_paren)

    pcb = PCBData(raw_content=content)

    # Parse version
    version_match = re.search(r"\(version\s+(\d+)\)", content)
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
        r"\(gr_rect\s*\(start\s+([\d.-]+)\s+([\d.-]+)\)\s*\(end\s+([\d.-]+)\s+([\d.-]+)\)",
        content,
        re.DOTALL,
    )
    if outline_match:
        x1, y1 = to_float(outline_match.group(1)), to_float(outline_match.group(2))
        x2, y2 = to_float(outline_match.group(3)), to_float(outline_match.group(4))
        pcb.board_outline = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

    # Parse footprints
    # More robust footprint parsing
    fp_starts = [m.start() for m in re.finditer(r'\(footprint\s+"', content)]

    for start in fp_starts:
        _, end = form_span(content, start)
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
        at_match = re.search(r"\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)", fp_text)
        if at_match:
            x, y = to_float(at_match.group(1)), to_float(at_match.group(2))
            rot = to_float(at_match.group(3))
        else:
            x, y, rot = 0.0, 0.0, 0.0

        # Extract layer
        layer_match = re.search(r'\(layer\s+"([^"]+)"\)', fp_text)
        layer = layer_match.group(1) if layer_match else "F.Cu"

        # Extract UUID
        uuid_match = re.search(r'\(uuid\s+"([^"]+)"\)', fp_text)
        uuid = uuid_match.group(1) if uuid_match else ""

        if reference:  # Only add if we found a reference
            pcb.footprints.append(
                Footprint(
                    reference=reference,
                    footprint_lib=lib_name,
                    position=(x, y),
                    rotation=rot,
                    layer=layer,
                    uuid=uuid,
                    raw_sexpr=fp_text,
                )
            )

    # Parse tracks
    for match in re.finditer(
        r"\(segment\s+\(start\s+([\d.-]+)\s+([\d.-]+)\)\s*"
        r"\(end\s+([\d.-]+)\s+([\d.-]+)\)\s*"
        r"\(width\s+([\d.-]+)\)\s*"
        r'\(layer\s+"([^"]+)"\)\s*'
        r"\(net\s+(\d+)\)",
        content,
    ):
        pcb.tracks.append(
            Track(
                start=(to_float(match.group(1)), to_float(match.group(2))),
                end=(to_float(match.group(3)), to_float(match.group(4))),
                width=to_float(match.group(5)),
                layer=match.group(6),
                net=int(match.group(7)),
            )
        )

    # Parse vias
    for match in re.finditer(
        r"\(via\s+\(at\s+([\d.-]+)\s+([\d.-]+)\)\s*"
        r"\(size\s+([\d.-]+)\)\s*"
        r"\(drill\s+([\d.-]+)\)\s*"
        r'\(layers\s+"([^"]+)"\s+"([^"]+)"\)\s*'
        r"\(net\s+(\d+)\)",
        content,
    ):
        pcb.vias.append(
            Via(
                position=(to_float(match.group(1)), to_float(match.group(2))),
                size=to_float(match.group(3)),
                drill=to_float(match.group(4)),
                layers=(match.group(5), match.group(6)),
                net=int(match.group(7)),
            )
        )

    logger.info(
        f"Parsed PCB: {len(pcb.footprints)} footprints, {len(pcb.tracks)} tracks, {len(pcb.vias)} vias"
    )
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
    layer: str = "F.Cu",
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
		(fp_text value "{footprint_lib.split(":")[-1]}" (at 0 2) (layer "{layer}")
			(effects (font (size 1 1) (thickness 0.15)))
		)
	)'''


def create_track_sexpr(
    start: tuple[float, float],
    end: tuple[float, float],
    width: float = 0.25,
    layer: str = "F.Cu",
    net: int = 0,
) -> str:
    """Generate S-expression for a track segment."""
    uuid = generate_uuid()
    return f'(segment (start {start[0]} {start[1]}) (end {end[0]} {end[1]}) (width {width}) (layer "{layer}") (net {net}) (uuid "{uuid}"))'


def create_via_sexpr(
    position: tuple[float, float],
    size: float = 0.8,
    drill: float = 0.4,
    layers: tuple[str, str] = ("F.Cu", "B.Cu"),
    net: int = 0,
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
    with open(pcb_path, encoding="utf-8") as f:
        content = f.read()

    # Find insertion point (before the final closing paren)
    # Look for last ) that closes the kicad_pcb
    insert_pos = content.rfind(")")

    if insert_pos == -1:
        raise ValueError("Invalid PCB file format")

    fp_sexpr = create_footprint_sexpr(
        reference=footprint.reference,
        footprint_lib=footprint.footprint_lib,
        position=footprint.position,
        rotation=footprint.rotation,
        layer=footprint.layer,
    )

    new_content = content[:insert_pos] + "\n\t" + fp_sexpr + "\n" + content[insert_pos:]

    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    logger.info(f"Added footprint {footprint.reference} at {footprint.position}")
    return True


def update_footprint_position(
    pcb_path: str,
    reference: str,
    new_position: tuple[float, float],
    new_rotation: float | None = None,
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
    with open(pcb_path, encoding="utf-8") as f:
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
    search_start = max(
        0, ref_match.start() - 10000
    )  # Look back up to 10000 chars for large footprints
    search_region = content[search_start : ref_match.start()]

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
    fp_header = content[fp_start : fp_start + 300]

    # Look for the uuid line first, then find (at after it
    uuid_match = re.search(r'\(uuid\s+"[^"]+"\)', fp_header)
    if uuid_match:
        # Search for (at) right after uuid
        after_uuid = fp_header[uuid_match.end() :]
        # The main (at) should be on the next line, with proper indentation (tab + tab or similar)
        at_pattern = re.compile(
            r"^\s*\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)", re.MULTILINE
        )
        at_match = at_pattern.search(after_uuid)

        if at_match:
            at_abs_start = fp_start + uuid_match.end() + at_match.start()
            at_abs_end = fp_start + uuid_match.end() + at_match.end()
        else:
            # Fallback: find first (at) in the header section
            at_pattern = re.compile(r"\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)")
            at_match = at_pattern.search(fp_header)
            if not at_match:
                logger.warning(f"Position not found for footprint {reference}")
                return False
            at_abs_start = fp_start + at_match.start()
            at_abs_end = fp_start + at_match.end()
    else:
        # No uuid found, use original fallback - find first (at) in the footprint header
        at_pattern = re.compile(r"\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)")
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
        new_at = f"(at {x} {y} {old_rot})" if old_rot else f"(at {x} {y})"

    new_content = content[:at_abs_start] + new_at + content[at_abs_end:]

    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    logger.info(f"Updated {reference} position to {new_position}")
    return True


def add_track_to_pcb(pcb_path: str, track: Track) -> bool:
    """Add a track segment to an existing PCB file."""
    with open(pcb_path, encoding="utf-8") as f:
        content = f.read()

    insert_pos = content.rfind(")")

    track_sexpr = create_track_sexpr(
        start=track.start, end=track.end, width=track.width, layer=track.layer, net=track.net
    )

    new_content = content[:insert_pos] + "\n\t" + track_sexpr + "\n" + content[insert_pos:]

    with open(pcb_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    logger.info(f"Added track from {track.start} to {track.end} on {track.layer}")
    return True


def add_via_to_pcb(pcb_path: str, via: Via) -> bool:
    """Add a via to an existing PCB file."""
    with open(pcb_path, encoding="utf-8") as f:
        content = f.read()

    insert_pos = content.rfind(")")

    via_sexpr = create_via_sexpr(
        position=via.position, size=via.size, drill=via.drill, layers=via.layers, net=via.net
    )

    new_content = content[:insert_pos] + "\n\t" + via_sexpr + "\n" + content[insert_pos:]

    with open(pcb_path, "w", encoding="utf-8") as f:
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
