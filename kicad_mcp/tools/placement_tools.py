"""
PCB Component Placement Optimization Tools

Implements simulated annealing-based placement optimization to improve
routability before running auto-routing (Freerouting).

Inspired by:
- dielectric (Princeton) - Computational geometry + simulated annealing
- ePlace - Electrostatic placement algorithms
- TimberWolf - Classic simulated annealing placer

Key optimization targets:
- Minimize total wirelength (Half-Perimeter Wirelength - HPWL)
- Keep connected components close together
- Avoid component overlap
- Respect board boundaries
"""

import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Set
from pathlib import Path


@dataclass
class PadInfo:
    """Pad position information relative to component center."""
    name: str
    rel_x: float  # Position relative to component center
    rel_y: float
    net_name: str
    
    def get_absolute_pos(self, comp_x: float, comp_y: float, comp_angle: float) -> Tuple[float, float]:
        """Get absolute pad position given component position and angle."""
        # Rotate relative position by component angle
        angle_rad = math.radians(comp_angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        
        rot_x = self.rel_x * cos_a - self.rel_y * sin_a
        rot_y = self.rel_x * sin_a + self.rel_y * cos_a
        
        return (comp_x + rot_x, comp_y + rot_y)


@dataclass
class ComponentInfo:
    """Component placement information with pad-level detail."""
    reference: str
    x: float
    y: float
    width: float
    height: float
    angle: float = 0.0
    layer: str = "F.Cu"
    locked: bool = False
    nets: Set[str] = field(default_factory=set)
    pads: List[PadInfo] = field(default_factory=list)  # Pad positions
    
    def get_pad_positions(self) -> Dict[str, Tuple[float, float]]:
        """Get absolute positions of all pads."""
        return {
            pad.name: pad.get_absolute_pos(self.x, self.y, self.angle)
            for pad in self.pads
        }
    
    def get_bounds(self) -> Tuple[float, float, float, float]:
        """Get bounding box (minx, miny, maxx, maxy) considering rotation."""
        # Simplified - assumes 0/90/180/270 degree rotations
        if self.angle in [90, 270]:
            w, h = self.height, self.width
        else:
            w, h = self.width, self.height
        
        return (
            self.x - w/2,
            self.y - h/2,
            self.x + w/2,
            self.y + h/2
        )
    
    def overlaps(self, other: 'ComponentInfo', clearance: float = 0.127) -> bool:
        """
        Check if this component overlaps with another.
        
        NOTE: The width/height should already be BOUNDING BOX sizes (including
        courtyard), so we only need a small clearance for the DRC minimum.
        Default 0.127mm matches KiCad's default netclass clearance.
        """
        b1 = self.get_bounds()
        b2 = other.get_bounds()
        
        # Add clearance
        return not (
            b1[2] + clearance < b2[0] or  # self right < other left
            b1[0] - clearance > b2[2] or  # self left > other right
            b1[3] + clearance < b2[1] or  # self top < other bottom
            b1[1] - clearance > b2[3]     # self bottom > other top
        )


@dataclass
class NetInfo:
    """Net connectivity information with pin locations."""
    name: str
    pins: List[Tuple[str, str]]  # List of (component_ref, pad_name)
    
    def compute_hpwl(self, components: Dict[str, 'ComponentInfo']) -> float:
        """
        Compute Half-Perimeter Wirelength using actual pad positions.
        
        This is more accurate than using component centers because it
        accounts for where the actual connections are on each component.
        """
        if len(self.pins) < 2:
            return 0.0
        
        xs = []
        ys = []
        
        for comp_ref, pad_name in self.pins:
            comp = components.get(comp_ref)
            if comp:
                # Find the pad and get its absolute position
                pad_pos = None
                for pad in comp.pads:
                    if pad.name == pad_name:
                        pad_pos = pad.get_absolute_pos(comp.x, comp.y, comp.angle)
                        break
                
                if pad_pos:
                    xs.append(pad_pos[0])
                    ys.append(pad_pos[1])
                else:
                    # Fallback to component center if pad not found
                    xs.append(comp.x)
                    ys.append(comp.y)
        
        if len(xs) >= 2:
            return (max(xs) - min(xs)) + (max(ys) - min(ys))
        return 0.0


@dataclass
class BoardInfo:
    """Board dimensions and constraints."""
    width: float
    height: float
    margin: float = 1.0  # Edge margin in mm


class PlacementScorer:
    """
    Scores a placement based on multiple metrics.
    
    Metrics:
    - HPWL (Half-Perimeter Wirelength): Sum of bounding box half-perimeters for all nets
    - Overlap penalty: Heavy penalty for overlapping components
    - Boundary penalty: Penalty for components outside board
    - Density balance: Penalize uneven component distribution
    """
    
    def __init__(
        self,
        components: Dict[str, ComponentInfo],
        nets: Dict[str, NetInfo],
        board: BoardInfo,
        weights: Optional[Dict[str, float]] = None
    ):
        self.components = components
        self.nets = nets
        self.board = board
        
        # Scoring weights
        self.weights = weights or {
            'wirelength': 1.0,
            'overlap': 1000.0,  # Heavy penalty
            'boundary': 500.0,
            'density': 0.1
        }
    
    def compute_hpwl(self) -> float:
        """
        Compute Half-Perimeter Wirelength for all nets using pad positions.
        
        HPWL = sum over all nets of (max_x - min_x + max_y - min_y)
        Uses actual pad positions for accuracy, not component centers.
        """
        total_hpwl = 0.0
        
        for net_name, net in self.nets.items():
            # Use the net's own HPWL computation with pad positions
            total_hpwl += net.compute_hpwl(self.components)
        
        return total_hpwl
    
    def compute_overlap_penalty(self, clearance: float = 0.127) -> float:
        """
        Compute penalty for overlapping components.
        
        Default clearance 0.127mm matches KiCad's default netclass clearance.
        Component sizes should already include courtyard/bounding box.
        """
        penalty = 0.0
        comp_list = list(self.components.values())
        
        for i, c1 in enumerate(comp_list):
            for c2 in comp_list[i+1:]:
                if c1.overlaps(c2, clearance):
                    # Penalty proportional to overlap area
                    b1 = c1.get_bounds()
                    b2 = c2.get_bounds()
                    
                    overlap_x = max(0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
                    overlap_y = max(0, min(b1[3], b2[3]) - max(b1[1], b2[1]))
                    overlap_area = overlap_x * overlap_y
                    
                    penalty += overlap_area + 1.0  # +1 for any overlap
        
        return penalty
    
    def compute_boundary_penalty(self) -> float:
        """Compute penalty for components outside board boundaries."""
        penalty = 0.0
        margin = self.board.margin
        
        for comp in self.components.values():
            bounds = comp.get_bounds()
            
            # Check each edge
            if bounds[0] < margin:
                penalty += (margin - bounds[0]) ** 2
            if bounds[1] < margin:
                penalty += (margin - bounds[1]) ** 2
            if bounds[2] > self.board.width - margin:
                penalty += (bounds[2] - (self.board.width - margin)) ** 2
            if bounds[3] > self.board.height - margin:
                penalty += (bounds[3] - (self.board.height - margin)) ** 2
        
        return penalty
    
    def compute_density_variance(self) -> float:
        """
        Compute variance in component density across board regions.
        Lower variance = more uniform distribution.
        """
        # Divide board into 3x3 grid
        grid_x = 3
        grid_y = 3
        cell_w = self.board.width / grid_x
        cell_h = self.board.height / grid_y
        
        # Count components in each cell
        counts = [[0] * grid_y for _ in range(grid_x)]
        
        for comp in self.components.values():
            cx = int(min(comp.x / cell_w, grid_x - 1))
            cy = int(min(comp.y / cell_h, grid_y - 1))
            cx = max(0, cx)
            cy = max(0, cy)
            counts[cx][cy] += 1
        
        # Compute variance
        all_counts = [counts[i][j] for i in range(grid_x) for j in range(grid_y)]
        mean = sum(all_counts) / len(all_counts)
        variance = sum((c - mean) ** 2 for c in all_counts) / len(all_counts)
        
        return variance
    
    def score(self) -> Tuple[float, Dict[str, float]]:
        """
        Compute total placement score (lower is better).
        
        Returns:
            (total_score, breakdown_dict)
        """
        hpwl = self.compute_hpwl()
        overlap = self.compute_overlap_penalty()
        boundary = self.compute_boundary_penalty()
        density = self.compute_density_variance()
        
        breakdown = {
            'wirelength': hpwl,
            'overlap': overlap,
            'boundary': boundary,
            'density': density
        }
        
        total = (
            self.weights['wirelength'] * hpwl +
            self.weights['overlap'] * overlap +
            self.weights['boundary'] * boundary +
            self.weights['density'] * density
        )
        
        return total, breakdown


class SimulatedAnnealingPlacer:
    """
    Simulated annealing-based component placement optimizer.
    
    Algorithm:
    1. Start with initial placement
    2. At each iteration:
       - Generate random move (translate or swap)
       - Compute score change (delta)
       - Accept if delta < 0 (improvement)
       - Accept with probability exp(-delta/T) if delta > 0
    3. Reduce temperature according to cooling schedule
    4. Repeat until temperature reaches minimum
    """
    
    def __init__(
        self,
        initial_temp: float = 100.0,
        final_temp: float = 0.1,
        cooling_rate: float = 0.95,
        iterations_per_temp: int = 50,
        max_iterations: int = 5000,
        seed: Optional[int] = None
    ):
        self.initial_temp = initial_temp
        self.final_temp = final_temp
        self.cooling_rate = cooling_rate
        self.iterations_per_temp = iterations_per_temp
        self.max_iterations = max_iterations
        
        if seed is not None:
            random.seed(seed)
    
    def _generate_move(
        self,
        components: Dict[str, ComponentInfo],
        board: BoardInfo,
        temperature: float
    ) -> Tuple[str, ...]:
        """
        Generate a random move.
        
        Move types:
        - 'translate': Move one component to new position
        - 'swap': Swap positions of two components
        - 'rotate': Rotate a component by 90 degrees
        
        Step size decreases with temperature for fine-tuning.
        """
        movable = [ref for ref, c in components.items() if not c.locked]
        
        if not movable:
            return ('none',)
        
        move_type = random.choices(
            ['translate', 'swap', 'rotate'],
            weights=[0.6, 0.25, 0.15]
        )[0]
        
        if move_type == 'translate':
            ref = random.choice(movable)
            comp = components[ref]
            
            # Step size proportional to temperature
            temp_ratio = temperature / self.initial_temp
            max_step = min(board.width, board.height) * 0.3 * temp_ratio
            max_step = max(max_step, 0.5)  # Minimum 0.5mm step
            
            dx = random.gauss(0, max_step)
            dy = random.gauss(0, max_step)
            
            new_x = comp.x + dx
            new_y = comp.y + dy
            
            # Clamp to board bounds
            margin = board.margin + max(comp.width, comp.height) / 2
            new_x = max(margin, min(board.width - margin, new_x))
            new_y = max(margin, min(board.height - margin, new_y))
            
            return ('translate', ref, new_x, new_y)
        
        elif move_type == 'swap' and len(movable) >= 2:
            ref1, ref2 = random.sample(movable, 2)
            return ('swap', ref1, ref2)
        
        elif move_type == 'rotate':
            ref = random.choice(movable)
            new_angle = (components[ref].angle + 90) % 360
            return ('rotate', ref, new_angle)
        
        return ('none',)
    
    def _apply_move(
        self,
        components: Dict[str, ComponentInfo],
        move: Tuple[str, ...]
    ) -> Optional[Tuple[str, ...]]:
        """
        Apply a move and return undo information.
        """
        move_type = move[0]
        
        if move_type == 'translate':
            _, ref, new_x, new_y = move
            comp = components[ref]
            old_x, old_y = comp.x, comp.y
            comp.x, comp.y = new_x, new_y
            return ('translate', ref, old_x, old_y)
        
        elif move_type == 'swap':
            _, ref1, ref2 = move
            c1, c2 = components[ref1], components[ref2]
            c1.x, c2.x = c2.x, c1.x
            c1.y, c2.y = c2.y, c1.y
            return ('swap', ref1, ref2)
        
        elif move_type == 'rotate':
            _, ref, new_angle = move
            comp = components[ref]
            old_angle = comp.angle
            comp.angle = new_angle
            return ('rotate', ref, old_angle)
        
        return None
    
    def _undo_move(
        self,
        components: Dict[str, ComponentInfo],
        undo: Tuple[str, ...]
    ):
        """Undo a move using the undo information."""
        self._apply_move(components, undo)
    
    def optimize(
        self,
        components: Dict[str, ComponentInfo],
        nets: Dict[str, NetInfo],
        board: BoardInfo,
        callback: Optional[callable] = None
    ) -> Dict:
        """
        Run simulated annealing optimization.
        
        Args:
            components: Dict of component references to ComponentInfo
            nets: Dict of net names to NetInfo
            board: Board dimensions
            callback: Optional callback(iteration, temp, score, best_score)
        
        Returns:
            Dict with optimization results and statistics
        """
        start_time = time.time()
        
        scorer = PlacementScorer(components, nets, board)
        current_score, _ = scorer.score()
        best_score = current_score
        best_positions = {
            ref: (c.x, c.y, c.angle)
            for ref, c in components.items()
        }
        
        temperature = self.initial_temp
        iteration = 0
        accepted = 0
        rejected = 0
        improvements = 0
        
        score_history = [current_score]
        temp_history = [temperature]
        
        while temperature > self.final_temp and iteration < self.max_iterations:
            for _ in range(self.iterations_per_temp):
                # Generate and apply move
                move = self._generate_move(components, board, temperature)
                undo = self._apply_move(components, move)
                
                if undo is None:
                    continue
                
                # Evaluate new score
                new_score, _ = scorer.score()
                delta = new_score - current_score
                
                # Accept or reject
                accept = False
                if delta < 0:
                    accept = True
                    improvements += 1
                elif temperature > 0:
                    prob = math.exp(-delta / temperature)
                    if random.random() < prob:
                        accept = True
                
                if accept:
                    current_score = new_score
                    accepted += 1
                    
                    # Track best solution
                    if current_score < best_score:
                        best_score = current_score
                        best_positions = {
                            ref: (c.x, c.y, c.angle)
                            for ref, c in components.items()
                        }
                else:
                    self._undo_move(components, undo)
                    rejected += 1
                
                iteration += 1
                
                if iteration % 100 == 0:
                    score_history.append(current_score)
                    temp_history.append(temperature)
                    
                    if callback:
                        callback(iteration, temperature, current_score, best_score)
            
            # Cool down
            temperature *= self.cooling_rate
        
        # Restore best solution
        for ref, (x, y, angle) in best_positions.items():
            components[ref].x = x
            components[ref].y = y
            components[ref].angle = angle
        
        # Final score breakdown
        final_score, breakdown = scorer.score()
        
        elapsed = time.time() - start_time
        
        return {
            'initial_score': score_history[0],
            'final_score': final_score,
            'improvement': score_history[0] - final_score,
            'improvement_percent': (score_history[0] - final_score) / score_history[0] * 100 if score_history[0] > 0 else 0,
            'iterations': iteration,
            'accepted': accepted,
            'rejected': rejected,
            'improvements': improvements,
            'acceptance_rate': accepted / (accepted + rejected) if (accepted + rejected) > 0 else 0,
            'elapsed_seconds': elapsed,
            'breakdown': breakdown,
            'score_history': score_history,
            'temp_history': temp_history
        }


def extract_actual_sizes_from_kicad(pcb_path: str) -> Optional[Dict]:
    """
    Extract ACTUAL component sizes from KiCad using pcbnew via subprocess.
    
    This is more accurate than estimate_footprint_size() because it uses
    real bounding boxes from the footprints.
    
    Args:
        pcb_path: Path to the .kicad_pcb file
        
    Returns:
        Dict with component sizes, or None if extraction fails
    """
    import subprocess
    import tempfile
    import os
    
    # Find KiCad Python
    kicad_python_paths = [
        r"C:\Program Files\KiCad\9.0\bin\python.exe",
        r"C:\Program Files\KiCad\8.0\bin\python.exe",
    ]
    kicad_python = None
    for path in kicad_python_paths:
        if os.path.exists(path):
            kicad_python = path
            break
    
    if not kicad_python:
        return None
    
    # Create temp file for output
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_path = f.name
    
    try:
        # Get the extract_sizes script path
        script_dir = Path(__file__).parent.parent / "utils"
        script_path = script_dir / "extract_sizes.py"
        
        if not script_path.exists():
            # Create inline script
            script = f'''
import json
import pcbnew

board = pcbnew.LoadBoard(r"{pcb_path}")

def nm_to_mm(nm):
    return nm / 1_000_000

bbox = board.GetBoardEdgesBoundingBox()
board_info = {{
    "left": nm_to_mm(bbox.GetLeft()),
    "top": nm_to_mm(bbox.GetTop()),  
    "right": nm_to_mm(bbox.GetRight()),
    "bottom": nm_to_mm(bbox.GetBottom()),
    "width": nm_to_mm(bbox.GetWidth()),
    "height": nm_to_mm(bbox.GetHeight())
}}

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
        "x": nm_to_mm(pos.x),
        "y": nm_to_mm(pos.y),
        "width": nm_to_mm(fp_bbox.GetWidth()),
        "height": nm_to_mm(fp_bbox.GetHeight()),
        "rotation": fp.GetOrientationDegrees(),
        "locked": fp.IsLocked(),
        "pads": pads
    }}

nets = {{}}
for ref, comp in components.items():
    for pad in comp["pads"]:
        net_name = pad["net"]
        if net_name and not net_name.startswith("unconnected"):
            if net_name not in nets:
                nets[net_name] = {{"pins": []}}
            nets[net_name]["pins"].append([ref, pad["name"]])

result = {{"success": True, "board": board_info, "components": components, "nets": nets}}

with open(r"{output_path}", "w") as f:
    json.dump(result, f)
'''
            result = subprocess.run(
                [kicad_python, "-c", script],
                capture_output=True,
                text=True,
                timeout=30
            )
        else:
            result = subprocess.run(
                [kicad_python, str(script_path), pcb_path, output_path],
                capture_output=True,
                text=True,
                timeout=30
            )
        
        if result.returncode != 0:
            return None
        
        # Read result
        with open(output_path, 'r') as f:
            return json.load(f)
            
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to extract sizes via KiCad: {e}")
        return None
    finally:
        # Clean up temp file
        try:
            os.unlink(output_path)
        except:
            pass


def extract_placement_from_parsed_pcb(pcb, pcb_path: str = None) -> Tuple[Dict[str, ComponentInfo], Dict[str, NetInfo], BoardInfo]:
    """
    Extract placement information from parsed PCB data.
    
    If pcb_path is provided, uses KiCad's pcbnew (via subprocess) to get 
    ACTUAL component bounding boxes. Otherwise falls back to estimates.
    
    Args:
        pcb: PCBData object from pcb_parser.parse_pcb_file()
        pcb_path: Optional path to .kicad_pcb file for accurate sizes
    
    Returns:
        (components_dict, nets_dict, board_info)
    """
    import re
    
    # Try to get actual sizes from KiCad
    actual_sizes = None
    if pcb_path:
        actual_sizes = extract_actual_sizes_from_kicad(pcb_path)
    
    # Determine board dimensions from board outline or footprint positions
    if pcb.board_outline:
        xs = [p[0] for p in pcb.board_outline]
        ys = [p[1] for p in pcb.board_outline]
        board_width = max(xs) - min(xs)
        board_height = max(ys) - min(ys)
        origin_x = min(xs)
        origin_y = min(ys)
    elif actual_sizes and actual_sizes.get("board"):
        # Use KiCad's board info
        board = actual_sizes["board"]
        origin_x = board["left"]
        origin_y = board["top"]
        board_width = board["width"]
        board_height = board["height"]
    else:
        # No board outline - estimate from component positions
        if pcb.footprints:
            xs = [fp.position[0] for fp in pcb.footprints]
            ys = [fp.position[1] for fp in pcb.footprints]
            # Add margin around components
            origin_x = min(xs) - 5
            origin_y = min(ys) - 5
            board_width = max(xs) - min(xs) + 10
            board_height = max(ys) - min(ys) + 10
        else:
            origin_x, origin_y = 0, 0
            board_width, board_height = 100, 100  # Default size
    
    board_info = BoardInfo(
        width=board_width,
        height=board_height,
        margin=1.0
    )
    
    # Extract components from footprints
    components = {}
    net_to_pins = {}  # Build net->pins mapping
    
    for fp in pcb.footprints:
        ref = fp.reference
        x = fp.position[0] - origin_x
        y = fp.position[1] - origin_y
        rot = fp.rotation
        layer = fp.layer
        
        # Get size: prefer actual sizes from KiCad, fall back to estimates
        if actual_sizes and ref in actual_sizes.get("components", {}):
            kc = actual_sizes["components"][ref]
            width = kc["width"]
            height = kc["height"]
            # Use KiCad's position (more accurate)
            x = kc["x"] - origin_x
            y = kc["y"] - origin_y
            rot = kc["rotation"]
            locked = kc["locked"]
            
            # Use KiCad's pad info
            pads = []
            pad_nets = set()
            for pad_data in kc.get("pads", []):
                net_name = pad_data["net"]
                if net_name:
                    pad_nets.add(net_name)
                    if net_name not in net_to_pins:
                        net_to_pins[net_name] = []
                    net_to_pins[net_name].append((ref, pad_data["name"]))
                
                pads.append(PadInfo(
                    name=pad_data["name"],
                    rel_x=pad_data["x_offset"],
                    rel_y=pad_data["y_offset"],
                    net_name=net_name
                ))
        else:
            # Fall back to estimate from library name
            width, height = estimate_footprint_size(fp.footprint_lib)
            locked = "(locked)" in fp.raw_sexpr.lower() if fp.raw_sexpr else False
            
            # Parse pads from raw s-expression
            pads = []
            pad_nets = set()
            
            pad_starts = [m.start() for m in re.finditer(r'\(pad\s+"', fp.raw_sexpr)]
            
            for pad_start in pad_starts:
                pad_text = fp.raw_sexpr[pad_start:pad_start + 500]
                
                name_match = re.search(r'\(pad\s+"([^"]+)"', pad_text)
                if not name_match:
                    continue
                pad_name = name_match.group(1)
                
                at_match = re.search(r'\(at\s+([\d.-]+)\s+([\d.-]+)', pad_text)
                if at_match:
                    pad_rel_x = float(at_match.group(1))
                    pad_rel_y = float(at_match.group(2))
                else:
                    pad_rel_x, pad_rel_y = 0.0, 0.0
                
                net_match = re.search(r'\(net\s+(\d+)\s+"([^"]*)"', pad_text)
                net_name = net_match.group(2) if net_match else ""
                
                if net_name:
                    pad_nets.add(net_name)
                    if net_name not in net_to_pins:
                        net_to_pins[net_name] = []
                    net_to_pins[net_name].append((ref, pad_name))
                
                pads.append(PadInfo(
                    name=pad_name,
                    rel_x=pad_rel_x,
                    rel_y=pad_rel_y,
                    net_name=net_name
                ))
        
        components[ref] = ComponentInfo(
            reference=ref,
            x=x,
            y=y,
            width=width,
            height=height,
            angle=rot,
            layer=layer,
            locked=locked,
            nets=pad_nets,
            pads=pads
        )
    
    # Build NetInfo objects
    nets = {}
    for net_name, pins in net_to_pins.items():
        if len(pins) >= 2:  # Only include nets with 2+ connections
            nets[net_name] = NetInfo(name=net_name, pins=pins)
    
    return components, nets, board_info


def estimate_footprint_size(footprint_lib: str) -> Tuple[float, float]:
    """
    Estimate footprint BOUNDING BOX dimensions from library name.
    
    NOTE: These are BOUNDING BOX sizes (including courtyard/silkscreen),
    NOT body sizes! KiCad's GetBoundingBox() returns the full extent
    including courtyard margins (~0.25-0.5mm on each side).
    
    Args:
        footprint_lib: Footprint library string like "Package_QFN:QFN-48-1EP_7x7mm_P0.5mm"
    
    Returns:
        (width, height) in mm - BOUNDING BOX size, not body size
    """
    import re
    
    # Try to extract dimensions from footprint name
    # Common patterns: 7x7mm, 5.0x5.0mm, 0603, 0402, etc.
    
    # Pattern 1: NxMmm format - add ~1mm for courtyard
    dim_match = re.search(r'(\d+\.?\d*)x(\d+\.?\d*)mm', footprint_lib, re.IGNORECASE)
    if dim_match:
        w = float(dim_match.group(1)) + 1.0  # Add courtyard margin
        h = float(dim_match.group(2)) + 1.0
        return w, h
    
    # Pattern 2: Imperial codes (0402, 0603, 0805, etc.)
    # These are BOUNDING BOX sizes from KiCad, NOT body sizes!
    # Body size 0402=1.0x0.5mm, but bbox=1.87x0.97mm
    imperial_bbox = {
        '0201': (1.3, 0.8),    # body 0.6x0.3, bbox ~1.3x0.8
        '0402': (1.9, 1.0),    # body 1.0x0.5, bbox 1.87x0.97
        '0603': (2.4, 1.5),    # body 1.6x0.8, bbox ~2.4x1.5
        '0805': (2.8, 2.0),    # body 2.0x1.25, bbox ~2.8x2.0
        '1206': (4.0, 2.4),    # body 3.2x1.6, bbox ~4.0x2.4
        '1210': (4.0, 3.2),    # body 3.2x2.5, bbox ~4.0x3.2
        '2512': (7.0, 4.0),    # body 6.3x3.2, bbox ~7.0x4.0
    }
    for code, size in imperial_bbox.items():
        if code in footprint_lib:
            return size
    
    # Pattern 3: QFN/QFP/LQFP with size - add ~2mm for courtyard
    qfn_match = re.search(r'(QFN|QFP|LQFP|TQFP).*?(\d+).*?(\d+\.?\d*)x(\d+\.?\d*)', footprint_lib, re.IGNORECASE)
    if qfn_match:
        w = float(qfn_match.group(3)) + 2.0  # Add courtyard for QFN
        h = float(qfn_match.group(4)) + 2.0
        return w, h
    
    # Pattern 4: SOT packages - use BOUNDING BOX sizes from KiCad!
    # SOT-23-5 body ~2.9x1.3mm, but KiCad bbox = 4.15x3.62mm
    if 'SOT-23' in footprint_lib.upper():
        return 4.2, 3.7  # Actual KiCad bounding box
    if 'SOT-223' in footprint_lib.upper():
        return 8.0, 5.0  # Estimated bbox
    
    # Pattern 5: USB connectors - use actual bounding box sizes
    if 'USB' in footprint_lib.upper():
        if 'TYPE-C' in footprint_lib.upper() or 'USB_C' in footprint_lib.upper():
            return 10.7, 9.5  # Actual KiCad bbox for USB-C
        return 8.0, 7.0
    
    # Pattern 6: Crystal - use bounding box sizes
    if 'Crystal' in footprint_lib or 'Resonator' in footprint_lib:
        if '3215' in footprint_lib:
            return 4.3, 2.4  # Crystal_SMD_3215 bbox
        if '2012' in footprint_lib:
            return 3.1, 2.3  # Crystal_SMD_2012 bbox
        return 4.0, 2.5  # Default crystal bbox
    
    # Default size for unknown footprints - be conservative
    return 3.0, 3.0


def extract_placement_from_kicad(board) -> Tuple[Dict[str, ComponentInfo], Dict[str, NetInfo], BoardInfo]:
    """
    Extract placement information from KiCad board object.
    
    Args:
        board: pcbnew.BOARD object
    
    Returns:
        (components_dict, nets_dict, board_info)
    """
    import pcbnew
    
    # Get board dimensions
    bbox = board.GetBoardEdgesBoundingBox()
    board_width = pcbnew.ToMM(bbox.GetWidth())
    board_height = pcbnew.ToMM(bbox.GetHeight())
    board_origin_x = pcbnew.ToMM(bbox.GetX())
    board_origin_y = pcbnew.ToMM(bbox.GetY())
    
    board_info = BoardInfo(
        width=board_width,
        height=board_height,
        margin=1.0
    )
    
    # Extract components
    components = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        pos = fp.GetPosition()
        bbox = fp.GetBoundingBox()
        
        # Get position relative to board origin
        x = pcbnew.ToMM(pos.x) - board_origin_x
        y = pcbnew.ToMM(pos.y) - board_origin_y
        
        width = pcbnew.ToMM(bbox.GetWidth())
        height = pcbnew.ToMM(bbox.GetHeight())
        angle = fp.GetOrientationDegrees()
        
        layer = "F.Cu" if fp.GetLayer() == pcbnew.F_Cu else "B.Cu"
        locked = fp.IsLocked()
        
        # Get nets and pad positions for this component
        nets = set()
        pads = []
        comp_pos_x = pcbnew.ToMM(pos.x)
        comp_pos_y = pcbnew.ToMM(pos.y)
        
        for pad in fp.Pads():
            net = pad.GetNet()
            net_name = ""
            if net:
                net_name = net.GetNetname() or ""
                if net_name:
                    nets.add(net_name)
            
            # Get pad position relative to component center
            pad_pos = pad.GetPosition()
            pad_x = pcbnew.ToMM(pad_pos.x)
            pad_y = pcbnew.ToMM(pad_pos.y)
            
            # Store relative position (before rotation - we'll apply rotation when computing)
            # Since KiCad gives us absolute position, we need to un-rotate to get relative
            rel_x = pad_x - comp_pos_x
            rel_y = pad_y - comp_pos_y
            
            # Un-rotate by component angle to get original relative position
            if angle != 0:
                angle_rad = math.radians(-angle)  # Negative to un-rotate
                cos_a = math.cos(angle_rad)
                sin_a = math.sin(angle_rad)
                orig_rel_x = rel_x * cos_a - rel_y * sin_a
                orig_rel_y = rel_x * sin_a + rel_y * cos_a
                rel_x, rel_y = orig_rel_x, orig_rel_y
            
            pads.append(PadInfo(
                name=pad.GetName(),
                rel_x=rel_x,
                rel_y=rel_y,
                net_name=net_name
            ))
        
        components[ref] = ComponentInfo(
            reference=ref,
            x=x,
            y=y,
            width=width,
            height=height,
            angle=angle,
            layer=layer,
            locked=locked,
            nets=nets,
            pads=pads
        )
    
    # Extract nets
    nets = {}
    netinfo_list = board.GetNetInfo()
    
    for net_item in netinfo_list.NetsByNetcode().values():
        net_name = net_item.GetNetname()
        if not net_name or net_name == "":
            continue
        
        pins = []
        for fp in board.GetFootprints():
            ref = fp.GetReference()
            for pad in fp.Pads():
                pad_net = pad.GetNet()
                if pad_net and pad_net.GetNetname() == net_name:
                    pins.append((ref, pad.GetName()))
        
        if len(pins) >= 2:  # Only nets with 2+ pins
            nets[net_name] = NetInfo(name=net_name, pins=pins)
    
    return components, nets, board_info


def apply_placement_to_file(pcb_path: str, components: Dict[str, ComponentInfo], origin: Tuple[float, float] = (0, 0)):
    """
    Apply optimized placement back to PCB file (file-based, no pcbnew).
    
    Args:
        pcb_path: Path to .kicad_pcb file
        components: Dict of optimized component placements
        origin: (x, y) origin offset to add back
    """
    from ..utils.pcb_parser import update_footprint_position
    
    origin_x, origin_y = origin
    
    for ref, comp in components.items():
        if comp.locked:
            continue
        
        # Convert back to absolute coordinates
        abs_x = comp.x + origin_x
        abs_y = comp.y + origin_y
        
        try:
            update_footprint_position(
                pcb_path=pcb_path,
                reference=ref,
                new_position=(abs_x, abs_y),
                new_rotation=comp.angle
            )
        except Exception as e:
            # Log but continue with other components
            print(f"Warning: Could not update {ref}: {e}")


def optimize_placement(
    project_path: str,
    iterations: int = 3000,
    initial_temp: float = 100.0,
    cooling_rate: float = 0.95,
    seed: Optional[int] = None
) -> Dict:
    """
    Main entry point for placement optimization (file-based, no pcbnew).
    
    Args:
        project_path: Path to KiCad project file (.kicad_pro)
        iterations: Maximum optimization iterations
        initial_temp: Initial annealing temperature
        cooling_rate: Temperature cooling rate (0.9-0.99)
        seed: Random seed for reproducibility
    
    Returns:
        Dict with optimization results
    """
    from ..utils.pcb_parser import parse_pcb_file
    
    # Find PCB file
    project_path = Path(project_path)
    if project_path.suffix == '.kicad_pro':
        pcb_path = project_path.with_suffix('.kicad_pcb')
    else:
        pcb_path = project_path
    
    if not pcb_path.exists():
        return {'error': f'PCB file not found: {pcb_path}'}
    
    # Parse PCB file
    pcb = parse_pcb_file(str(pcb_path))
    
    # Get board origin from outline or estimate
    if pcb.board_outline:
        xs = [p[0] for p in pcb.board_outline]
        ys = [p[1] for p in pcb.board_outline]
        board_origin = (min(xs), min(ys))
    else:
        # Estimate from footprint positions
        if pcb.footprints:
            xs = [fp.position[0] for fp in pcb.footprints]
            ys = [fp.position[1] for fp in pcb.footprints]
            board_origin = (min(xs) - 5, min(ys) - 5)
        else:
            board_origin = (0, 0)
    
    # Extract current placement with ACTUAL sizes from KiCad
    components, nets, board_info = extract_placement_from_parsed_pcb(pcb, str(pcb_path))
    
    # Count movable components
    movable_count = sum(1 for c in components.values() if not c.locked)
    
    if movable_count == 0:
        return {
            'error': 'No movable components found (all locked)',
            'total_components': len(components)
        }
    
    # Create optimizer
    placer = SimulatedAnnealingPlacer(
        initial_temp=initial_temp,
        final_temp=0.1,
        cooling_rate=cooling_rate,
        iterations_per_temp=max(10, movable_count),
        max_iterations=iterations,
        seed=seed
    )
    
    # Run optimization
    results = placer.optimize(components, nets, board_info)
    
    # Apply optimized placement to file
    apply_placement_to_file(str(pcb_path), components, board_origin)
    
    results['pcb_path'] = str(pcb_path)
    results['total_components'] = len(components)
    results['movable_components'] = movable_count
    results['total_nets'] = len(nets)
    results['board_size'] = f'{board_info.width:.1f}x{board_info.height:.1f}mm'
    
    return results


# =============================================================================
# MCP Tool Registration
# =============================================================================

def register_placement_tools(mcp):
    """Register placement optimization tools with MCP server."""
    
    @mcp.tool()
    async def optimize_component_placement(
        project_path: str,
        iterations: int = 3000,
        initial_temp: float = 100.0,
        cooling_rate: float = 0.95
    ) -> str:
        """
        Optimize PCB component placement using simulated annealing.
        
        This tool improves component positions to minimize wirelength and
        improve routability. Run this BEFORE auto-routing for better results.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
            iterations: Maximum optimization iterations (default 3000)
            initial_temp: Initial annealing temperature (default 100.0)
            cooling_rate: Temperature cooling rate 0.9-0.99 (default 0.95)
        
        Returns:
            Optimization results including score improvement
        """
        try:
            results = optimize_placement(
                project_path=project_path,
                iterations=iterations,
                initial_temp=initial_temp,
                cooling_rate=cooling_rate
            )
            
            if 'error' in results:
                return f"Error: {results['error']}"
            
            return f"""Placement Optimization Complete!

Board: {results['board_size']}
Components: {results['total_components']} total, {results['movable_components']} movable
Nets: {results['total_nets']}

Results:
- Initial Score: {results['initial_score']:.2f}
- Final Score: {results['final_score']:.2f}
- Improvement: {results['improvement']:.2f} ({results['improvement_percent']:.1f}%)

Statistics:
- Iterations: {results['iterations']}
- Accepted moves: {results['accepted']}
- Rejected moves: {results['rejected']}
- Acceptance rate: {results['acceptance_rate']:.1%}
- Elapsed time: {results['elapsed_seconds']:.1f}s

Score Breakdown:
- Wirelength (HPWL): {results['breakdown']['wirelength']:.2f}mm
- Overlap penalty: {results['breakdown']['overlap']:.2f}
- Boundary penalty: {results['breakdown']['boundary']:.2f}
- Density variance: {results['breakdown']['density']:.2f}

Saved to: {results['pcb_path']}

Tip: Run Freerouting now for auto-routing with improved placement."""
            
        except Exception as e:
            return f"Error optimizing placement: {str(e)}"
    
    @mcp.tool()
    async def analyze_placement_quality(project_path: str) -> str:
        """
        Analyze current PCB placement quality without modifying it.
        
        Computes metrics like wirelength, overlap, and density distribution
        to assess placement quality and identify improvement opportunities.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
        
        Returns:
            Placement quality analysis report
        """
        from ..utils.pcb_parser import parse_pcb_file
        
        try:
            # Find PCB file
            project_path = Path(project_path)
            if project_path.suffix == '.kicad_pro':
                pcb_path = project_path.with_suffix('.kicad_pcb')
            else:
                pcb_path = project_path
            
            if not pcb_path.exists():
                return f"Error: PCB file not found: {pcb_path}"
            
            # Parse PCB file directly (no pcbnew needed)
            pcb = parse_pcb_file(str(pcb_path))
            
            # Extract placement from parsed data with ACTUAL sizes from KiCad
            components, nets, board_info = extract_placement_from_parsed_pcb(pcb, str(pcb_path))
            
            if not components:
                return "Error: No components found in PCB. Did you run Update PCB from Schematic (F8)?"
            
            # Score current placement
            scorer = PlacementScorer(components, nets, board_info)
            score, breakdown = scorer.score()
            
            # Analyze nets
            net_lengths = []
            for net_name, net in nets.items():
                if len(net.pins) >= 2:
                    xs = []
                    ys = []
                    for comp_ref, _ in net.pins:
                        comp = components.get(comp_ref)
                        if comp:
                            xs.append(comp.x)
                            ys.append(comp.y)
                    if len(xs) >= 2:
                        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
                        net_lengths.append((net_name, hpwl, len(net.pins)))
            
            # Sort by length
            net_lengths.sort(key=lambda x: x[1], reverse=True)
            
            # Find overlapping components
            overlaps = []
            comp_list = list(components.values())
            for i, c1 in enumerate(comp_list):
                for c2 in comp_list[i+1:]:
                    if c1.overlaps(c2, 0.127):  # Match KiCad's default clearance
                        overlaps.append((c1.reference, c2.reference))
            
            # Count locked vs unlocked
            locked_count = sum(1 for c in components.values() if c.locked)
            unlocked_count = len(components) - locked_count
            
            report = f"""PCB Placement Analysis
{'='*50}

Board: {board_info.width:.1f} x {board_info.height:.1f} mm
Components: {len(components)} ({unlocked_count} movable, {locked_count} locked)
Nets: {len(nets)} (with 2+ pins)

Overall Score: {score:.2f} (lower is better)

Metrics:
- Total Wirelength (HPWL): {breakdown['wirelength']:.2f} mm
- Overlap Penalty: {breakdown['overlap']:.2f}
- Boundary Penalty: {breakdown['boundary']:.2f}
- Density Variance: {breakdown['density']:.2f}

"""
            
            if overlaps:
                report += f"⚠️  Overlapping Components ({len(overlaps)} pairs):\n"
                for ref1, ref2 in overlaps[:5]:
                    report += f"   - {ref1} ↔ {ref2}\n"
                if len(overlaps) > 5:
                    report += f"   ... and {len(overlaps)-5} more\n"
                report += "\n"
            else:
                report += "✓ No overlapping components\n\n"
            
            report += f"Top 10 Longest Nets (candidates for optimization):\n"
            for name, length, pins in net_lengths[:10]:
                report += f"   {name}: {length:.1f}mm ({pins} pins)\n"
            
            report += f"""
Recommendations:
"""
            if breakdown['overlap'] > 0:
                report += "- ⚠️  Fix component overlaps before routing\n"
            if breakdown['wirelength'] > 500:
                report += "- Consider running placement optimization to reduce wirelength\n"
            if breakdown['density'] > 5:
                report += "- Components are unevenly distributed, optimization may help\n"
            if score < 100:
                report += "- ✓ Placement looks reasonable, ready for routing\n"
            
            return report
            
        except Exception as e:
            import traceback
            return f"Error analyzing placement: {str(e)}\n{traceback.format_exc()}"
    
    @mcp.tool()
    async def quick_placement_optimization(project_path: str) -> str:
        """
        Run a quick placement optimization (fewer iterations, faster).
        
        Good for rapid iteration during design. For final optimization,
        use optimize_component_placement with more iterations.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
        
        Returns:
            Quick optimization results
        """
        try:
            results = optimize_placement(
                project_path=project_path,
                iterations=1000,
                initial_temp=50.0,
                cooling_rate=0.92
            )
            
            if 'error' in results:
                return f"Error: {results['error']}"
            
            return f"""Quick Placement Optimization Complete!

Improvement: {results['improvement_percent']:.1f}%
- Initial: {results['initial_score']:.2f} → Final: {results['final_score']:.2f}
- Time: {results['elapsed_seconds']:.1f}s
- Wirelength: {results['breakdown']['wirelength']:.1f}mm

Saved to: {results['pcb_path']}"""
            
        except Exception as e:
            return f"Error: {str(e)}"
    
    @mcp.tool()
    async def suggest_placement_fixes(project_path: str) -> str:
        """
        Analyze PCB placement and suggest specific fixes for problematic nets.
        
        Identifies nets with long wirelength and suggests which components
        to move closer together. Provides actionable recommendations.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
        
        Returns:
            Detailed placement fix suggestions
        """
        from ..utils.pcb_parser import parse_pcb_file
        
        try:
            # Find PCB file
            project_path = Path(project_path)
            if project_path.suffix == '.kicad_pro':
                pcb_path = project_path.with_suffix('.kicad_pcb')
            else:
                pcb_path = project_path
            
            if not pcb_path.exists():
                return f"Error: PCB file not found: {pcb_path}"
            
            # Parse PCB file (no pcbnew needed)
            pcb = parse_pcb_file(str(pcb_path))
            
            # Extract placement from parsed data with ACTUAL sizes from KiCad
            components, nets, board_info = extract_placement_from_parsed_pcb(pcb, str(pcb_path))
            
            report = []
            report.append("=" * 60)
            report.append("PLACEMENT FIX SUGGESTIONS")
            report.append("=" * 60)
            report.append("")
            
            # Analyze each net in detail
            net_analysis = []
            for net_name, net in nets.items():
                if len(net.pins) < 2:
                    continue
                
                # Calculate HPWL using pad positions
                hpwl = net.compute_hpwl(components)
                
                # Get all pin positions for this net
                pin_positions = []
                for comp_ref, pad_name in net.pins:
                    comp = components.get(comp_ref)
                    if comp:
                        for pad in comp.pads:
                            if pad.name == pad_name:
                                abs_pos = pad.get_absolute_pos(comp.x, comp.y, comp.angle)
                                pin_positions.append({
                                    'comp': comp_ref,
                                    'pad': pad_name,
                                    'x': abs_pos[0],
                                    'y': abs_pos[1]
                                })
                                break
                
                if len(pin_positions) >= 2:
                    # Find the two furthest apart pins
                    max_dist = 0
                    furthest_pair = None
                    for i, p1 in enumerate(pin_positions):
                        for p2 in pin_positions[i+1:]:
                            dist = math.sqrt((p1['x'] - p2['x'])**2 + (p1['y'] - p2['y'])**2)
                            if dist > max_dist:
                                max_dist = dist
                                furthest_pair = (p1, p2)
                    
                    net_analysis.append({
                        'name': net_name,
                        'hpwl': hpwl,
                        'pins': len(net.pins),
                        'max_distance': max_dist,
                        'furthest_pair': furthest_pair,
                        'all_pins': pin_positions
                    })
            
            # Sort by HPWL
            net_analysis.sort(key=lambda x: x['hpwl'], reverse=True)
            
            # Categorize nets
            power_nets = [n for n in net_analysis if n['name'] in ['GND', 'VCC', 'V1V8', '3V3', '5V', 'VBAT']]
            signal_nets = [n for n in net_analysis if n not in power_nets]
            
            # Power net recommendations
            report.append("🔌 POWER NETS")
            report.append("-" * 40)
            if power_nets:
                for pn in power_nets[:5]:
                    report.append(f"\n{pn['name']} ({pn['pins']} pins, HPWL: {pn['hpwl']:.1f}mm)")
                    if pn['name'] == 'GND':
                        report.append("  → Recommendation: Use ground pour (copper fill)")
                        report.append("  → Already added B.Cu ground pour ✓")
                    elif pn['name'] in ['VCC', 'V1V8', '3V3']:
                        report.append("  → Recommendation: Consider power pour or wide traces")
                        report.append("  → Group power components closer together")
            else:
                report.append("No power nets found")
            
            report.append("")
            report.append("📡 SIGNAL NETS (Top 10 longest)")
            report.append("-" * 40)
            
            for sn in signal_nets[:10]:
                report.append(f"\n{sn['name']} ({sn['pins']} pins, HPWL: {sn['hpwl']:.1f}mm)")
                
                if sn['furthest_pair']:
                    p1, p2 = sn['furthest_pair']
                    report.append(f"  Furthest: {p1['comp']}.{p1['pad']} ↔ {p2['comp']}.{p2['pad']}")
                    report.append(f"  Distance: {sn['max_distance']:.1f}mm")
                    
                    # Suggest fix based on distance
                    if sn['max_distance'] > 20:
                        report.append(f"  ⚠️  CRITICAL: Move {p1['comp']} closer to {p2['comp']}")
                        # Suggest new position
                        mid_x = (p1['x'] + p2['x']) / 2
                        mid_y = (p1['y'] + p2['y']) / 2
                        report.append(f"     Consider placing one near ({mid_x:.1f}, {mid_y:.1f})")
                    elif sn['max_distance'] > 10:
                        report.append(f"  ⚡ Consider moving {p1['comp']} or {p2['comp']} closer")
            
            report.append("")
            report.append("=" * 60)
            report.append("SUMMARY")
            report.append("=" * 60)
            
            critical_count = sum(1 for n in signal_nets if n['max_distance'] > 20)
            moderate_count = sum(1 for n in signal_nets if 10 < n['max_distance'] <= 20)
            
            report.append(f"Total nets analyzed: {len(net_analysis)}")
            report.append(f"Critical issues (>20mm): {critical_count}")
            report.append(f"Moderate issues (10-20mm): {moderate_count}")
            report.append("")
            
            if critical_count > 0:
                report.append("🔴 Action required: Fix critical nets before routing")
            elif moderate_count > 0:
                report.append("🟡 Optional: Optimize moderate nets for better routing")
            else:
                report.append("🟢 Placement looks good for routing!")
            
            report.append("")
            report.append("Next steps:")
            report.append("1. Fix any critical placement issues manually")
            report.append("2. Run Freerouting for auto-routing")
            report.append("3. Review remaining unrouted nets")
            
            return "\n".join(report)
            
        except Exception as e:
            import traceback
            return f"Error analyzing placement: {str(e)}\n{traceback.format_exc()}"


if __name__ == "__main__":
    # Test with a project path if run directly
    import sys
    if len(sys.argv) > 1:
        results = optimize_placement(sys.argv[1])
        print(results)
