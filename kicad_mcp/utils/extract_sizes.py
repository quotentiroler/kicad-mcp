#!/usr/bin/env python
"""
Standalone script to extract actual component sizes from KiCad PCB.

Run with KiCad's Python:
  "C:\Program Files\KiCad\9.0\bin\python.exe" extract_sizes.py <pcb_file> <output_json>

Outputs a JSON file with component bounding box dimensions.
"""

import json
import sys
import os

def main():
    if len(sys.argv) < 3:
        print("Usage: extract_sizes.py <pcb_file> <output_json>", file=sys.stderr)
        sys.exit(1)
    
    pcb_path = sys.argv[1]
    output_path = sys.argv[2]
    
    try:
        import pcbnew
    except ImportError:
        print("Error: pcbnew not available. Run with KiCad's Python.", file=sys.stderr)
        sys.exit(1)
    
    board = pcbnew.LoadBoard(pcb_path)
    
    def nm_to_mm(nm):
        return nm / 1_000_000
    
    # Get board bounds
    bbox = board.GetBoardEdgesBoundingBox()
    board_info = {
        "left": nm_to_mm(bbox.GetLeft()),
        "top": nm_to_mm(bbox.GetTop()),
        "right": nm_to_mm(bbox.GetRight()),
        "bottom": nm_to_mm(bbox.GetBottom()),
        "width": nm_to_mm(bbox.GetWidth()),
        "height": nm_to_mm(bbox.GetHeight())
    }
    
    # Extract components with ACTUAL bounding boxes
    components = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        pos = fp.GetPosition()
        fp_bbox = fp.GetBoundingBox(False, False)  # Without text/drawings
        
        # Get actual width/height from bounding box
        width = nm_to_mm(fp_bbox.GetWidth())
        height = nm_to_mm(fp_bbox.GetHeight())
        
        # Pad info for net extraction
        pads = []
        for pad in fp.Pads():
            pad_pos = pad.GetPosition()
            net = pad.GetNet()
            net_name = net.GetNetname() if net else ""
            pads.append({
                "name": pad.GetNumber(),
                "x_offset": nm_to_mm(pad_pos.x - pos.x),
                "y_offset": nm_to_mm(pad_pos.y - pos.y),
                "net": net_name
            })
        
        components[ref] = {
            "footprint": fp.GetFPIDAsString(),
            "x": nm_to_mm(pos.x),
            "y": nm_to_mm(pos.y),
            "width": width,
            "height": height,
            "rotation": fp.GetOrientationDegrees(),
            "locked": fp.IsLocked(),
            "bbox": {
                "left": nm_to_mm(fp_bbox.GetLeft()),
                "top": nm_to_mm(fp_bbox.GetTop()),
                "right": nm_to_mm(fp_bbox.GetRight()),
                "bottom": nm_to_mm(fp_bbox.GetBottom())
            },
            "pads": pads
        }
    
    # Build nets
    nets = {}
    for ref, comp in components.items():
        for pad in comp["pads"]:
            net_name = pad["net"]
            if net_name and not net_name.startswith("unconnected"):
                if net_name not in nets:
                    nets[net_name] = {"pins": []}
                nets[net_name]["pins"].append([ref, pad["name"]])
    
    result = {
        "success": True,
        "board": board_info,
        "components": components,
        "nets": {k: v for k, v in nets.items() if len(v["pins"]) >= 2}  # Only multi-pin nets
    }
    
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)
    
    print(f"Extracted {len(components)} components, {len(result['nets'])} nets to {output_path}")

if __name__ == "__main__":
    main()
