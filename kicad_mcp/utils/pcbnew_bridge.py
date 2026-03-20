"""
Bridge to KiCad's pcbnew module via subprocess.

Since pcbnew is only available in KiCad's embedded Python, this module
provides a way to call pcbnew operations from the MCP server's venv
by spawning KiCad's Python as a subprocess.
"""

import json
import subprocess
import sys
import os
from pathlib import Path
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Find KiCad's Python executable
KICAD_PYTHON_PATHS = [
    r"C:\Program Files\KiCad\9.0\bin\python.exe",
    r"C:\Program Files\KiCad\8.0\bin\python.exe",
    r"C:\Program Files\KiCad\bin\python.exe",
    "/usr/bin/python3",  # Linux - may need adjustment
    "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3",  # macOS
]


def find_kicad_python() -> Optional[str]:
    """Find KiCad's Python executable."""
    for path in KICAD_PYTHON_PATHS:
        if os.path.exists(path):
            return path
    return None


KICAD_PYTHON = find_kicad_python()


def run_pcbnew_script(script: str, timeout: int = 60) -> Dict[str, Any]:
    """
    Run a Python script using KiCad's Python interpreter.
    
    The script should print a JSON result to stdout.
    
    Args:
        script: Python code to execute (must print JSON to stdout)
        timeout: Timeout in seconds
        
    Returns:
        Parsed JSON result from the script
    """
    if not KICAD_PYTHON:
        return {"error": "KiCad Python not found. Install KiCad or set KICAD_PYTHON_PATH."}
    
    try:
        result = subprocess.run(
            [KICAD_PYTHON, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd()
        )
        
        if result.returncode != 0:
            return {
                "error": f"Script failed: {result.stderr}",
                "stdout": result.stdout,
                "returncode": result.returncode
            }
        
        # Parse JSON output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "error": "Failed to parse script output as JSON",
                "stdout": result.stdout,
                "stderr": result.stderr
            }
            
    except subprocess.TimeoutExpired:
        return {"error": f"Script timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


def load_board_info(pcb_path: str) -> Dict[str, Any]:
    """
    Load board information using pcbnew.
    
    Returns component positions, net info, and board bounds.
    """
    script = f'''
import json
import pcbnew

pcb_path = r"{pcb_path}"
board = pcbnew.LoadBoard(pcb_path)

def nm_to_mm(nm):
    return nm / 1_000_000

# Get board bounds
bbox = board.GetBoardEdgesBoundingBox()
board_info = {{
    "left": nm_to_mm(bbox.GetLeft()),
    "top": nm_to_mm(bbox.GetTop()),
    "right": nm_to_mm(bbox.GetRight()),
    "bottom": nm_to_mm(bbox.GetBottom()),
    "width": nm_to_mm(bbox.GetWidth()),
    "height": nm_to_mm(bbox.GetHeight())
}}

# Extract components
components = {{}}
for fp in board.GetFootprints():
    ref = fp.GetReference()
    pos = fp.GetPosition()
    fp_bbox = fp.GetBoundingBox(False, False)
    
    pads = []
    for pad in fp.Pads():
        pad_pos = pad.GetPosition()
        net = pad.GetNet()
        net_name = net.GetNetname() if net else ""
        pads.append({{
            "name": pad.GetNumber(),
            "x_offset": nm_to_mm(pad_pos.x - pos.x),
            "y_offset": nm_to_mm(pad_pos.y - pos.y),
            "net": net_name
        }})
    
    components[ref] = {{
        "footprint": fp.GetFPIDAsString(),
        "x": nm_to_mm(pos.x),
        "y": nm_to_mm(pos.y),
        "rotation": fp.GetOrientationDegrees(),
        "locked": fp.IsLocked(),
        "bbox": {{
            "left": nm_to_mm(fp_bbox.GetLeft()),
            "top": nm_to_mm(fp_bbox.GetTop()),
            "right": nm_to_mm(fp_bbox.GetRight()),
            "bottom": nm_to_mm(fp_bbox.GetBottom())
        }},
        "pads": pads
    }}

# Extract nets
nets = {{}}
for ref, comp in components.items():
    for pad in comp["pads"]:
        net_name = pad["net"]
        if net_name and not net_name.startswith("unconnected"):
            if net_name not in nets:
                nets[net_name] = {{"pins": []}}
            nets[net_name]["pins"].append((ref, pad["name"]))

result = {{
    "success": True,
    "board": board_info,
    "components": components,
    "nets": nets
}}

print(json.dumps(result))
'''
    return run_pcbnew_script(script)


def save_component_positions(pcb_path: str, positions: Dict[str, Dict]) -> Dict[str, Any]:
    """
    Save component positions using pcbnew.
    
    Args:
        pcb_path: Path to PCB file
        positions: Dict of ref -> {x, y, rotation}
    """
    positions_json = json.dumps(positions)
    
    script = f'''
import json
import pcbnew

pcb_path = r"{pcb_path}"
positions = json.loads(r'{positions_json}')

board = pcbnew.LoadBoard(pcb_path)

moved = 0
for fp in board.GetFootprints():
    ref = fp.GetReference()
    if ref in positions and not fp.IsLocked():
        pos = positions[ref]
        new_x = pcbnew.FromMM(pos["x"])
        new_y = pcbnew.FromMM(pos["y"])
        fp.SetPosition(pcbnew.VECTOR2I(int(new_x), int(new_y)))
        if "rotation" in pos:
            fp.SetOrientationDegrees(pos["rotation"])
        moved += 1

board.Save(pcb_path)

print(json.dumps({{"success": True, "moved": moved}}))
'''
    return run_pcbnew_script(script)


def analyze_placement(pcb_path: str) -> Dict[str, Any]:
    """
    Analyze placement quality using pcbnew.
    
    Returns wirelength, overlaps, density metrics.
    """
    script = f'''
import json
import math
import pcbnew

pcb_path = r"{pcb_path}"
board = pcbnew.LoadBoard(pcb_path)

def nm_to_mm(nm):
    return nm / 1_000_000

# Get board bounds
bbox = board.GetBoardEdgesBoundingBox()
board_width = nm_to_mm(bbox.GetWidth())
board_height = nm_to_mm(bbox.GetHeight())
origin_x = nm_to_mm(bbox.GetX())
origin_y = nm_to_mm(bbox.GetY())

# Extract component positions
components = {{}}
for fp in board.GetFootprints():
    ref = fp.GetReference()
    pos = fp.GetPosition()
    fp_bbox = fp.GetBoundingBox(False, False)
    
    components[ref] = {{
        "x": nm_to_mm(pos.x) - origin_x,
        "y": nm_to_mm(pos.y) - origin_y,
        "width": nm_to_mm(fp_bbox.GetWidth()),
        "height": nm_to_mm(fp_bbox.GetHeight()),
        "locked": fp.IsLocked()
    }}

# Build net-to-pins mapping
nets = {{}}
for fp in board.GetFootprints():
    ref = fp.GetReference()
    for pad in fp.Pads():
        net = pad.GetNet()
        if net:
            net_name = net.GetNetname()
            if net_name and not net_name.startswith("unconnected"):
                if net_name not in nets:
                    nets[net_name] = []
                nets[net_name].append(ref)

# Calculate total wirelength (HPWL)
total_hpwl = 0
net_lengths = []
for net_name, refs in nets.items():
    if len(refs) >= 2:
        unique_refs = list(set(refs))
        xs = [components[r]["x"] for r in unique_refs if r in components]
        ys = [components[r]["y"] for r in unique_refs if r in components]
        if len(xs) >= 2:
            hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
            total_hpwl += hpwl
            net_lengths.append({{"name": net_name, "length": hpwl, "pins": len(unique_refs)}})

# Sort nets by length
net_lengths.sort(key=lambda x: x["length"], reverse=True)

# Check for overlaps
overlaps = []
comp_list = list(components.items())
for i, (ref1, c1) in enumerate(comp_list):
    for ref2, c2 in comp_list[i+1:]:
        # Simple overlap check
        dx = abs(c1["x"] - c2["x"])
        dy = abs(c1["y"] - c2["y"])
        min_dx = (c1["width"] + c2["width"]) / 2 + 0.2
        min_dy = (c1["height"] + c2["height"]) / 2 + 0.2
        if dx < min_dx and dy < min_dy:
            overlaps.append((ref1, ref2))

# Count locked vs unlocked
locked = sum(1 for c in components.values() if c["locked"])
unlocked = len(components) - locked

result = {{
    "success": True,
    "board_size": f"{{board_width:.1f}} x {{board_height:.1f}} mm",
    "components": len(components),
    "locked": locked,
    "unlocked": unlocked,
    "nets": len(nets),
    "total_wirelength": round(total_hpwl, 2),
    "overlaps": overlaps[:10],
    "overlap_count": len(overlaps),
    "longest_nets": net_lengths[:10]
}}

print(json.dumps(result))
'''
    return run_pcbnew_script(script)


def propose_layouts(pcb_path: str, iterations: int = 3000) -> Dict[str, Any]:
    """
    Generate placement proposals using pcbnew.
    """
    script = f'''
import json
import math
import random
import copy
import pcbnew

pcb_path = r"{pcb_path}"
iterations = {iterations}

board = pcbnew.LoadBoard(pcb_path)

def nm_to_mm(nm):
    return nm / 1_000_000

# Get board bounds
bbox = board.GetBoardEdgesBoundingBox()
board_left = nm_to_mm(bbox.GetLeft())
board_top = nm_to_mm(bbox.GetTop())
board_right = nm_to_mm(bbox.GetRight())
board_bottom = nm_to_mm(bbox.GetBottom())
board_width = board_right - board_left
board_height = board_bottom - board_top

# Extract components
components = {{}}
for fp in board.GetFootprints():
    ref = fp.GetReference()
    pos = fp.GetPosition()
    fp_bbox = fp.GetBoundingBox(False, False)
    
    pads = []
    for pad in fp.Pads():
        net = pad.GetNet()
        net_name = net.GetNetname() if net else ""
        if net_name and not net_name.startswith("unconnected"):
            pads.append(net_name)
    
    components[ref] = {{
        "x": nm_to_mm(pos.x),
        "y": nm_to_mm(pos.y),
        "width": nm_to_mm(fp_bbox.GetWidth()),
        "height": nm_to_mm(fp_bbox.GetHeight()),
        "rotation": fp.GetOrientationDegrees(),
        "locked": fp.IsLocked(),
        "nets": list(set(pads))
    }}

# Build nets
nets = {{}}
for ref, comp in components.items():
    for net_name in comp["nets"]:
        if net_name not in nets:
            nets[net_name] = []
        nets[net_name].append(ref)

def calc_wirelength(comps):
    total = 0
    for net_name, refs in nets.items():
        if len(refs) >= 2:
            xs = [comps[r]["x"] for r in refs if r in comps]
            ys = [comps[r]["y"] for r in refs if r in comps]
            if len(xs) >= 2:
                total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total

# Current score
current_wl = calc_wirelength(components)

# Simple optimization: move components closer to their connected components
optimized = copy.deepcopy(components)
movable = [ref for ref, c in optimized.items() if not c["locked"]]

if movable:
    for _ in range(iterations):
        ref = random.choice(movable)
        comp = optimized[ref]
        
        # Find connected components
        connected = set()
        for net_name in comp["nets"]:
            connected.update(nets.get(net_name, []))
        connected.discard(ref)
        
        if connected:
            # Move toward centroid of connected components
            cx = sum(optimized[r]["x"] for r in connected if r in optimized) / len(connected)
            cy = sum(optimized[r]["y"] for r in connected if r in optimized) / len(connected)
            
            # Random step toward centroid
            step = random.uniform(0.1, 1.0)
            new_x = comp["x"] + (cx - comp["x"]) * step * 0.1
            new_y = comp["y"] + (cy - comp["y"]) * step * 0.1
            
            # Keep within board bounds
            margin = max(comp["width"], comp["height"]) / 2 + 0.5
            new_x = max(board_left + margin, min(board_right - margin, new_x))
            new_y = max(board_top + margin, min(board_bottom - margin, new_y))
            
            # Try the move
            old_x, old_y = comp["x"], comp["y"]
            comp["x"], comp["y"] = new_x, new_y
            
            new_wl = calc_wirelength(optimized)
            if new_wl > current_wl * 1.1:  # Reject if much worse
                comp["x"], comp["y"] = old_x, old_y

optimized_wl = calc_wirelength(optimized)

# Build result
moves = []
for ref, comp in optimized.items():
    orig = components[ref]
    if not comp["locked"] and (abs(comp["x"] - orig["x"]) > 0.1 or abs(comp["y"] - orig["y"]) > 0.1):
        moves.append({{
            "reference": ref,
            "x": round(comp["x"], 3),
            "y": round(comp["y"], 3),
            "rotation": comp["rotation"]
        }})

improvement = round((current_wl - optimized_wl) / max(current_wl, 1) * 100, 1)

result = {{
    "success": True,
    "current_scores": {{"wirelength": round(current_wl, 1)}},
    "proposals": [{{
        "name": "Wirelength Optimized",
        "description": "Moves components closer to their connected neighbors",
        "scores": {{"wirelength": round(optimized_wl, 1)}},
        "improvement": f"{{improvement:+.1f}}% wirelength",
        "component_moves": moves
    }}]
}}

print(json.dumps(result))
'''
    return run_pcbnew_script(script, timeout=120)


if __name__ == "__main__":
    # Test
    import sys
    if len(sys.argv) > 1:
        pcb = sys.argv[1]
    else:
        pcb = r"c:\Users\MaximilianNussbaumer\Workspace\sleeptracker\hardware\SOM_Band\SOM_Band.kicad_pcb"
    
    print("Testing pcbnew bridge...")
    print(f"KiCad Python: {KICAD_PYTHON}")
    
    result = analyze_placement(pcb)
    print(json.dumps(result, indent=2))
