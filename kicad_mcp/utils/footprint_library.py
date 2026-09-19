"""Building a placeable footprint from an installed KiCad library.

Reads the footprint definition KiCad ships, then rewrites the fields that
are specific to this board: reference, position, layer and uuid.
"""

import logging
import os
import re

from .pcb_parser import create_footprint_sexpr, generate_uuid

logger = logging.getLogger(__name__)


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
    if ":" not in footprint_lib:
        return None

    lib_name, fp_name = footprint_lib.split(":", 1)

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
        with open(fp_file, encoding="utf-8") as f:
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
    layer: str = "F.Cu",
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
        r'\(footprint\s+"[^"]*"', f'(footprint "{footprint_lib}"', lib_content, count=1
    )

    # "(layer" also matches a pad's "(layers ...)", which is not the footprint's
    if re.search(r'\(layer\s+"[^"]+"\)', modified):
        modified = re.sub(r'\(layer\s+"[^"]+"\)', f'(layer "{layer}")', modified, count=1)
    else:
        # Add layer after footprint name
        modified = re.sub(
            r'(\(footprint\s+"[^"]+")', f'\\1\n\t\t(layer "{layer}")', modified, count=1
        )

    # Update or add UUID
    if "(uuid" in modified:
        modified = re.sub(r'\(uuid\s+"[^"]+"\)', f'(uuid "{uuid}")', modified, count=1)
    else:
        modified = re.sub(r'(\(layer\s+"[^"]+"\))', f'\\1\n\t\t(uuid "{uuid}")', modified, count=1)

    # Update or add position
    if "(at " in modified:
        modified = re.sub(
            r"\(at\s+[\d.-]+\s+[\d.-]+(?:\s+[\d.-]+)?\)",
            f"(at {x} {y}{rot_str})",
            modified,
            count=1,
        )
    else:
        modified = re.sub(
            r'(\(uuid\s+"[^"]+"\))', f"\\1\n\t\t(at {x} {y}{rot_str})", modified, count=1
        )

    # Update reference text
    modified = re.sub(
        r'\(fp_text\s+reference\s+"[^"]*"', f'(fp_text reference "{reference}"', modified, count=1
    )

    return modified


def import_footprints_from_schematic(
    pcb_path: str,
    schematic_components: list[dict],
    board_width: float = 35.0,
    board_height: float = 25.0,
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
    with open(pcb_path, encoding="utf-8") as f:
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

    insert_pos = content.rfind(")")
    new_footprints = []

    for i, comp in enumerate(schematic_components):
        ref = comp.get("reference", "")
        footprint = comp.get("footprint", "")

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
            reference=ref, footprint_lib=footprint, position=(x, y), rotation=0.0, layer="F.Cu"
        )

        if fp_sexpr:
            new_footprints.append(fp_sexpr)
            added.append({"reference": ref, "footprint": footprint, "position": (x, y)})
        else:
            failed.append({"reference": ref, "reason": "Could not create footprint"})

    # Add all new footprints to PCB
    if new_footprints:
        all_footprints = "\n\n\t".join(new_footprints)
        new_content = content[:insert_pos] + "\n\n\t" + all_footprints + "\n" + content[insert_pos:]

        with open(pcb_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    return {
        "added": len(added),
        "skipped": len(skipped),
        "failed": len(failed),
        "details": {"added": added, "skipped": skipped, "failed": failed},
    }
