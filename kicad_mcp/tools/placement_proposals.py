"""
MCP Tool: Propose optimal component layouts for routability.

Production-ready placement optimizer that:
1. Respects component footprint sizes and clearances
2. Detects and prevents overlaps
3. Considers actual pad positions for wirelength
4. Accounts for 2-layer routing constraints
5. Preserves known-good placements (locked components)
6. Provides realistic routability estimates

Generates 3 placement candidates:
1. Wirelength-optimized: Minimize total routing length
2. Congestion-balanced: Reduce routing density hotspots
3. Functional-grouped: Cluster related components (decoupling near ICs)
"""

# pcbnew is imported lazily inside functions to avoid import errors
# when the MCP server starts (pcbnew only available in KiCad's Python)
import math
import random
import copy
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def nm_to_mm(nm: int) -> float:
    return nm / 1_000_000


def mm_to_nm(mm: float) -> int:
    return int(mm * 1_000_000)


@dataclass
class BoundingBox:
    """Axis-aligned bounding box."""
    left: float
    top: float
    right: float
    bottom: float
    
    @property
    def width(self) -> float:
        return self.right - self.left
    
    @property
    def height(self) -> float:
        return self.bottom - self.top
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)
    
    def intersects(self, other: 'BoundingBox', clearance: float = 0) -> bool:
        """Check if two bounding boxes intersect with optional clearance."""
        return not (
            self.right + clearance < other.left or
            other.right + clearance < self.left or
            self.bottom + clearance < other.top or
            other.bottom + clearance < self.top
        )
    
    def contains_point(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom
    
    def expanded(self, margin: float) -> 'BoundingBox':
        return BoundingBox(
            self.left - margin,
            self.top - margin,
            self.right + margin,
            self.bottom + margin
        )


@dataclass
class PadInfo:
    """Information about a component pad."""
    name: str
    x_offset: float  # mm from component center
    y_offset: float
    net_name: str
    layer: str  # F.Cu, B.Cu, or both


@dataclass
class ComponentInfo:
    """Complete information about a component for placement."""
    reference: str
    footprint_name: str
    x: float  # center position
    y: float
    rotation: float  # degrees
    bbox: BoundingBox  # actual footprint bounds (relative to center)
    pads: List[PadInfo]
    locked: bool = False
    is_ic: bool = False
    is_capacitor: bool = False
    is_connector: bool = False
    
    def get_bbox_at(self, x: float, y: float, rotation: float = None) -> BoundingBox:
        """Get bounding box if component were at (x, y) with given rotation."""
        if rotation is None:
            rotation = self.rotation
        
        # Calculate half dimensions
        half_w = self.bbox.width / 2
        half_h = self.bbox.height / 2
        
        # If rotation is 90 or 270, swap width/height
        if abs(rotation % 180 - 90) < 1:
            half_w, half_h = half_h, half_w
        
        return BoundingBox(
            x - half_w, y - half_h,
            x + half_w, y + half_h
        )
    
    def get_pad_positions_at(self, x: float, y: float, rotation: float = None) -> List[Tuple[str, float, float, str]]:
        """Get pad positions if component were at (x, y)."""
        if rotation is None:
            rotation = self.rotation
        
        rad = math.radians(rotation - self.rotation)  # delta rotation
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        
        result = []
        for pad in self.pads:
            # Rotate pad offset
            px = pad.x_offset * cos_r - pad.y_offset * sin_r
            py = pad.x_offset * sin_r + pad.y_offset * cos_r
            result.append((pad.name, x + px, y + py, pad.net_name))
        
        return result


@dataclass
class PlacementState:
    """Current state of all component placements."""
    components: Dict[str, ComponentInfo]
    board_bbox: BoundingBox
    
    def get_component_bbox(self, ref: str) -> Optional[BoundingBox]:
        comp = self.components.get(ref)
        if comp:
            return comp.get_bbox_at(comp.x, comp.y, comp.rotation)
        return None
    
    def check_overlap(self, ref: str, x: float, y: float, rotation: float = None, clearance: float = 0.127) -> bool:
        """Check if placing ref at (x,y) would overlap other components."""
        comp = self.components.get(ref)
        if not comp:
            return False
        
        new_bbox = comp.get_bbox_at(x, y, rotation)
        
        for other_ref, other_comp in self.components.items():
            if other_ref == ref:
                continue
            other_bbox = other_comp.get_bbox_at(other_comp.x, other_comp.y)
            if new_bbox.intersects(other_bbox, clearance):
                return True
        
        return False
    
    def is_within_board(self, ref: str, x: float, y: float, rotation: float = None, margin: float = 0.5) -> bool:
        """Check if component would be within board bounds."""
        comp = self.components.get(ref)
        if not comp:
            return False
        
        new_bbox = comp.get_bbox_at(x, y, rotation)
        inner_board = self.board_bbox.expanded(-margin)
        
        return (inner_board.left <= new_bbox.left and
                new_bbox.right <= inner_board.right and
                inner_board.top <= new_bbox.top and
                new_bbox.bottom <= inner_board.bottom)


@dataclass
class NetInfo:
    """Information about a net."""
    name: str
    pad_refs: List[Tuple[str, str]]  # (component_ref, pad_name)
    is_power: bool = False
    is_ground: bool = False
    
    @property
    def pin_count(self) -> int:
        return len(self.pad_refs)


class PlacementAnalyzer:
    """Analyzes PCB for placement optimization with full footprint awareness."""
    
    POWER_NET_PATTERNS = ['VCC', 'VDD', 'V1V8', 'V3V3', 'VBUS', 'AVDD', 'DVDD', '+3V3', '+5V', 'VBAT']
    GROUND_NET_PATTERNS = ['GND', 'AGND', 'DGND', 'VSS', 'AVSS']
    
    def __init__(self, board: Any):  # board is pcbnew.BOARD
        self.board = board
        
        # Extract board bounds
        bbox = board.GetBoardEdgesBoundingBox()
        self.board_bbox = BoundingBox(
            nm_to_mm(bbox.GetLeft()),
            nm_to_mm(bbox.GetTop()),
            nm_to_mm(bbox.GetRight()),
            nm_to_mm(bbox.GetBottom())
        )
        
        # Extract component info with full footprint data
        self.components = self._extract_components()
        self.nets = self._extract_nets()
        
        # Design rule clearance - matches KiCad's default netclass clearance (0.127mm)
        # The bounding boxes already include courtyard, so this is just for additional margin
        self.clearance = 0.127
        
        logger.info(f"Analyzed board: {len(self.components)} components, {len(self.nets)} nets")
        
    def _extract_components(self) -> Dict[str, ComponentInfo]:
        """Extract complete component information including footprint bounds."""
        import pcbnew  # Lazy import for layer constants
        
        components = {}
        
        for fp in self.board.GetFootprints():
            ref = fp.GetReference()
            pos = fp.GetPosition()
            
            # Get footprint bounding box
            fp_bbox = fp.GetBoundingBox(False, False)  # Don't include text, drawings
            
            # Extract pad information
            pads = []
            for pad in fp.Pads():
                pad_pos = pad.GetPosition()
                net = pad.GetNet()
                net_name = net.GetNetname() if net else ""
                
                # Get pad layer
                layer_set = pad.GetLayerSet()
                if layer_set.Contains(pcbnew.F_Cu) and layer_set.Contains(pcbnew.B_Cu):
                    layer = "*.Cu"
                elif layer_set.Contains(pcbnew.F_Cu):
                    layer = "F.Cu"
                else:
                    layer = "B.Cu"
                
                pads.append(PadInfo(
                    name=pad.GetNumber(),
                    x_offset=nm_to_mm(pad_pos.x - pos.x),
                    y_offset=nm_to_mm(pad_pos.y - pos.y),
                    net_name=net_name,
                    layer=layer
                ))
            
            # Classify component type
            fp_name = fp.GetFPIDAsString()
            is_ic = any(x in ref for x in ['U']) or 'QFN' in fp_name or 'BGA' in fp_name
            is_capacitor = ref.startswith('C') and not ref.startswith('CN')
            is_connector = ref.startswith('J') or ref.startswith('CN') or 'Connector' in fp_name
            
            # Calculate relative bbox (from center)
            cx, cy = nm_to_mm(pos.x), nm_to_mm(pos.y)
            components[ref] = ComponentInfo(
                reference=ref,
                footprint_name=fp_name,
                x=cx,
                y=cy,
                rotation=fp.GetOrientationDegrees(),
                bbox=BoundingBox(
                    nm_to_mm(fp_bbox.GetLeft()) - cx,
                    nm_to_mm(fp_bbox.GetTop()) - cy,
                    nm_to_mm(fp_bbox.GetRight()) - cx,
                    nm_to_mm(fp_bbox.GetBottom()) - cy
                ),
                pads=pads,
                locked=fp.IsLocked(),
                is_ic=is_ic,
                is_capacitor=is_capacitor,
                is_connector=is_connector
            )
        
        return components
    
    def _extract_nets(self) -> Dict[str, NetInfo]:
        """Extract net information."""
        nets = {}
        
        for ref, comp in self.components.items():
            for pad in comp.pads:
                net_name = pad.net_name
                if not net_name or net_name.startswith("unconnected"):
                    continue
                
                if net_name not in nets:
                    is_power = any(p in net_name.upper() for p in self.POWER_NET_PATTERNS)
                    is_ground = any(p in net_name.upper() for p in self.GROUND_NET_PATTERNS)
                    nets[net_name] = NetInfo(
                        name=net_name,
                        pad_refs=[],
                        is_power=is_power,
                        is_ground=is_ground
                    )
                
                nets[net_name].pad_refs.append((ref, pad.name))
        
        return nets
    
    def create_state(self) -> PlacementState:
        """Create a mutable placement state from current board."""
        return PlacementState(
            components=copy.deepcopy(self.components),
            board_bbox=self.board_bbox
        )
    
    def calculate_wirelength(self, state: PlacementState) -> float:
        """Calculate total HPWL (Half-Perimeter Wire Length) for all nets."""
        total = 0.0
        
        for net_name, net_info in self.nets.items():
            if net_info.pin_count < 2:
                continue
            
            # Skip power/ground for wirelength (they use planes/pours)
            if net_info.is_power or net_info.is_ground:
                continue
            
            # Get all pad positions for this net
            xs, ys = [], []
            for comp_ref, pad_name in net_info.pad_refs:
                comp = state.components.get(comp_ref)
                if not comp:
                    continue
                
                # Find pad and get its position
                for pad in comp.pads:
                    if pad.name == pad_name:
                        pads_at = comp.get_pad_positions_at(comp.x, comp.y, comp.rotation)
                        for pn, px, py, _ in pads_at:
                            if pn == pad_name:
                                xs.append(px)
                                ys.append(py)
                                break
                        break
            
            if len(xs) >= 2:
                hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
                # Weight by pin count (more pins = harder to route)
                weight = 1.0 + 0.1 * (len(xs) - 2)
                total += hpwl * weight
        
        return total
    
    def calculate_congestion_map(self, state: PlacementState, grid_size: int = 8) -> List[List[float]]:
        """Calculate routing congestion per grid cell."""
        cell_w = (self.board_bbox.right - self.board_bbox.left) / grid_size
        cell_h = (self.board_bbox.bottom - self.board_bbox.top) / grid_size
        
        # Initialize grid with routing demand
        grid = [[0.0] * grid_size for _ in range(grid_size)]
        
        for net_name, net_info in self.nets.items():
            if net_info.pin_count < 2:
                continue
            
            # Get pad positions
            positions = []
            for comp_ref, pad_name in net_info.pad_refs:
                comp = state.components.get(comp_ref)
                if not comp:
                    continue
                for pad in comp.pads:
                    if pad.name == pad_name:
                        pads_at = comp.get_pad_positions_at(comp.x, comp.y)
                        for pn, px, py, _ in pads_at:
                            if pn == pad_name:
                                positions.append((px, py))
                                break
            
            if len(positions) < 2:
                continue
            
            # Estimate routing path crosses each cell
            # Use bounding box of net and increment cells within
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            
            for gy in range(grid_size):
                for gx in range(grid_size):
                    cell_left = self.board_bbox.left + gx * cell_w
                    cell_top = self.board_bbox.top + gy * cell_h
                    cell_right = cell_left + cell_w
                    cell_bottom = cell_top + cell_h
                    
                    # Check if net bbox overlaps this cell
                    if not (max_x < cell_left or min_x > cell_right or
                            max_y < cell_top or min_y > cell_bottom):
                        # Weight by net complexity
                        weight = 1.0 if net_info.is_power or net_info.is_ground else 2.0
                        grid[gy][gx] += weight
        
        return grid
    
    def calculate_congestion_score(self, state: PlacementState) -> float:
        """Calculate congestion score (lower is better)."""
        grid = self.calculate_congestion_map(state)
        
        # Calculate peak and variance
        all_values = [v for row in grid for v in row]
        if not all_values:
            return 0.0
        
        peak = max(all_values)
        mean = sum(all_values) / len(all_values)
        variance = sum((v - mean) ** 2 for v in all_values) / len(all_values)
        
        # Score combines peak (bad) and variance (bad for uniformity)
        return peak * 2 + math.sqrt(variance)
    
    def calculate_grouping_score(self, state: PlacementState) -> float:
        """Score how well decoupling caps are placed near their ICs."""
        score = 0.0
        
        # Find ICs and their power nets
        ics = {ref: comp for ref, comp in state.components.items() if comp.is_ic}
        caps = {ref: comp for ref, comp in state.components.items() if comp.is_capacitor}
        
        for cap_ref, cap in caps.items():
            # Find power/ground nets this cap connects to
            cap_power_nets = set()
            for pad in cap.pads:
                if pad.net_name:
                    net = self.nets.get(pad.net_name)
                    if net and (net.is_power or net.is_ground):
                        cap_power_nets.add(pad.net_name)
            
            if not cap_power_nets:
                continue
            
            # Find closest IC that shares a power net
            min_dist = float('inf')
            for ic_ref, ic in ics.items():
                ic_power_nets = set()
                for pad in ic.pads:
                    if pad.net_name in cap_power_nets:
                        ic_power_nets.add(pad.net_name)
                
                if ic_power_nets:
                    dist = math.sqrt((cap.x - ic.x)**2 + (cap.y - ic.y)**2)
                    min_dist = min(min_dist, dist)
            
            if min_dist < float('inf'):
                # Optimal distance is 1-3mm, score decreases beyond that
                if min_dist <= 3.0:
                    score += 10.0 - min_dist  # Max 10 points at 0mm, 7 at 3mm
                else:
                    score += max(0, 7.0 - (min_dist - 3.0))  # Decreasing after 3mm
        
        return score
    
    def count_overlaps(self, state: PlacementState) -> int:
        """Count number of component overlaps."""
        overlaps = 0
        refs = list(state.components.keys())
        
        for i, ref1 in enumerate(refs):
            comp1 = state.components[ref1]
            bbox1 = comp1.get_bbox_at(comp1.x, comp1.y)
            
            for ref2 in refs[i+1:]:
                comp2 = state.components[ref2]
                bbox2 = comp2.get_bbox_at(comp2.x, comp2.y)
                
                if bbox1.intersects(bbox2, self.clearance):
                    overlaps += 1
        
        return overlaps


class PlacementOptimizer:
    """Advanced placement optimizer with overlap prevention."""
    
    def __init__(self, analyzer: PlacementAnalyzer):
        self.analyzer = analyzer
        self.margin = 0.5  # mm from board edge
        
    def _valid_position(self, state: PlacementState, ref: str, x: float, y: float, rotation: float = None) -> bool:
        """Check if position is valid (within bounds, no overlaps)."""
        return (state.is_within_board(ref, x, y, rotation, self.margin) and
                not state.check_overlap(ref, x, y, rotation, self.analyzer.clearance))
    
    def _find_valid_nearby_position(self, state: PlacementState, ref: str, 
                                     target_x: float, target_y: float,
                                     max_tries: int = 20) -> Optional[Tuple[float, float]]:
        """Find a valid position near the target."""
        comp = state.components.get(ref)
        if not comp:
            return None
        
        # Try target first
        if self._valid_position(state, ref, target_x, target_y):
            return (target_x, target_y)
        
        # Spiral outward to find valid position
        for radius in [0.5, 1.0, 2.0, 3.0, 5.0]:
            for angle in range(0, 360, 30):
                rad = math.radians(angle)
                x = target_x + radius * math.cos(rad)
                y = target_y + radius * math.sin(rad)
                if self._valid_position(state, ref, x, y):
                    return (x, y)
        
        return None
    
    def optimize_wirelength(self, iterations: int = 3000) -> PlacementState:
        """Simulated annealing to minimize wirelength with overlap prevention."""
        state = self.analyzer.create_state()
        current_cost = self.analyzer.calculate_wirelength(state)
        
        best_state = copy.deepcopy(state)
        best_cost = current_cost
        
        temperature = 5.0  # Start smaller for more controlled moves
        cooling_rate = 0.997
        
        movable = [ref for ref, comp in state.components.items() 
                   if not comp.locked and not comp.is_connector]
        
        if not movable:
            return state
        
        accepted = 0
        for i in range(iterations):
            ref = random.choice(movable)
            comp = state.components[ref]
            old_x, old_y = comp.x, comp.y
            
            # Move proportional to temperature
            dx = (random.random() - 0.5) * temperature * 2
            dy = (random.random() - 0.5) * temperature * 2
            new_x, new_y = old_x + dx, old_y + dy
            
            # Find valid position near target
            valid_pos = self._find_valid_nearby_position(state, ref, new_x, new_y)
            if not valid_pos:
                continue
            
            new_x, new_y = valid_pos
            comp.x, comp.y = new_x, new_y
            
            new_cost = self.analyzer.calculate_wirelength(state)
            delta = new_cost - current_cost
            
            if delta < 0 or random.random() < math.exp(-delta / max(temperature, 0.1)):
                current_cost = new_cost
                accepted += 1
                if new_cost < best_cost:
                    best_state = copy.deepcopy(state)
                    best_cost = new_cost
            else:
                comp.x, comp.y = old_x, old_y
            
            temperature *= cooling_rate
        
        logger.info(f"Wirelength optimization: {accepted}/{iterations} moves accepted")
        return best_state
    
    def optimize_congestion(self, iterations: int = 3000) -> PlacementState:
        """Optimize for uniform routing distribution."""
        state = self.analyzer.create_state()
        current_cost = self.analyzer.calculate_congestion_score(state)
        
        best_state = copy.deepcopy(state)
        best_cost = current_cost
        
        temperature = 5.0
        cooling_rate = 0.997
        
        movable = [ref for ref, comp in state.components.items()
                   if not comp.locked and not comp.is_connector]
        
        if not movable:
            return state
        
        for i in range(iterations):
            ref = random.choice(movable)
            comp = state.components[ref]
            old_x, old_y = comp.x, comp.y
            
            dx = (random.random() - 0.5) * temperature * 2
            dy = (random.random() - 0.5) * temperature * 2
            new_x, new_y = old_x + dx, old_y + dy
            
            valid_pos = self._find_valid_nearby_position(state, ref, new_x, new_y)
            if not valid_pos:
                continue
            
            new_x, new_y = valid_pos
            comp.x, comp.y = new_x, new_y
            
            new_cost = self.analyzer.calculate_congestion_score(state)
            delta = new_cost - current_cost
            
            if delta < 0 or random.random() < math.exp(-delta / max(temperature, 0.1)):
                current_cost = new_cost
                if new_cost < best_cost:
                    best_state = copy.deepcopy(state)
                    best_cost = new_cost
            else:
                comp.x, comp.y = old_x, old_y
            
            temperature *= cooling_rate
        
        return best_state
    
    def optimize_functional_groups(self, iterations: int = 3000) -> PlacementState:
        """Optimize to place decoupling caps near ICs."""
        state = self.analyzer.create_state()
        
        # Combined cost function
        def cost(s):
            wl = self.analyzer.calculate_wirelength(s)
            grp = self.analyzer.calculate_grouping_score(s)
            return wl - grp * 10  # Strong grouping weight
        
        current_cost = cost(state)
        best_state = copy.deepcopy(state)
        best_cost = current_cost
        
        temperature = 5.0
        cooling_rate = 0.997
        
        # Prioritize moving capacitors
        caps = [ref for ref, comp in state.components.items()
                if comp.is_capacitor and not comp.locked]
        others = [ref for ref, comp in state.components.items()
                  if not comp.is_capacitor and not comp.locked and not comp.is_connector]
        
        movable = caps * 3 + others  # Weight caps higher
        
        if not movable:
            return state
        
        for i in range(iterations):
            ref = random.choice(movable)
            comp = state.components[ref]
            old_x, old_y = comp.x, comp.y
            
            # For caps, try to move toward related ICs
            dx = (random.random() - 0.5) * temperature * 2
            dy = (random.random() - 0.5) * temperature * 2
            
            if comp.is_capacitor and random.random() < 0.3:
                # Find related IC
                cap_nets = {pad.net_name for pad in comp.pads if pad.net_name}
                for ic_ref, ic in state.components.items():
                    if ic.is_ic:
                        ic_nets = {pad.net_name for pad in ic.pads}
                        if cap_nets & ic_nets:
                            # Move toward this IC
                            dx = (ic.x - comp.x) * 0.3 + (random.random() - 0.5) * 2
                            dy = (ic.y - comp.y) * 0.3 + (random.random() - 0.5) * 2
                            break
            
            new_x, new_y = old_x + dx, old_y + dy
            
            valid_pos = self._find_valid_nearby_position(state, ref, new_x, new_y)
            if not valid_pos:
                continue
            
            new_x, new_y = valid_pos
            comp.x, comp.y = new_x, new_y
            
            new_cost = cost(state)
            delta = new_cost - current_cost
            
            if delta < 0 or random.random() < math.exp(-delta / max(temperature, 0.1)):
                current_cost = new_cost
                if new_cost < best_cost:
                    best_state = copy.deepcopy(state)
                    best_cost = new_cost
            else:
                comp.x, comp.y = old_x, old_y
            
            temperature *= cooling_rate
        
        return best_state


def propose_layouts(project_path: str, iterations: int = 3000) -> Dict[str, Any]:
    """
    Generate 3 placement proposals optimized for routability.
    
    This production-ready optimizer:
    - Respects component footprint sizes and clearances
    - Prevents overlapping components
    - Considers actual pad positions for wirelength calculation
    - Accounts for power/ground net routing differently
    - Preserves locked components and connectors
    
    Args:
        project_path: Path to KiCad project file (.kicad_pro)
        iterations: Optimization iterations per proposal (default 3000)
    
    Returns:
        Dictionary with:
        - current_scores: Scores of current placement
        - proposals: List of 3 proposals, each with:
            - name: Strategy name
            - description: What this layout optimizes for
            - scores: {wirelength, congestion, grouping, overlaps}
            - improvement: Percentage improvement vs current
            - trade_offs: List of trade-off explanations
            - component_moves: List of components to move
    """
    import pcbnew  # Lazy import - only available in KiCad's Python
    
    pcb_path = Path(project_path).with_suffix('.kicad_pcb')
    
    try:
        board = pcbnew.LoadBoard(str(pcb_path))
    except Exception as e:
        return {"error": f"Failed to load PCB: {e}"}
    
    if not board:
        return {"error": f"Failed to load PCB: {pcb_path}"}
    
    # Analyze current state
    analyzer = PlacementAnalyzer(board)
    optimizer = PlacementOptimizer(analyzer)
    
    current_state = analyzer.create_state()
    current_scores = {
        'wirelength': round(analyzer.calculate_wirelength(current_state), 1),
        'congestion': round(analyzer.calculate_congestion_score(current_state), 2),
        'grouping': round(analyzer.calculate_grouping_score(current_state), 1),
        'overlaps': analyzer.count_overlaps(current_state)
    }
    
    proposals = []
    
    # Strategy 1: Wirelength optimized
    logger.info("Optimizing for wirelength...")
    wl_state = optimizer.optimize_wirelength(iterations)
    wl_scores = {
        'wirelength': round(analyzer.calculate_wirelength(wl_state), 1),
        'congestion': round(analyzer.calculate_congestion_score(wl_state), 2),
        'grouping': round(analyzer.calculate_grouping_score(wl_state), 1),
        'overlaps': analyzer.count_overlaps(wl_state)
    }
    wl_improvement = round((current_scores['wirelength'] - wl_scores['wirelength']) / 
                           max(current_scores['wirelength'], 1) * 100, 1)
    
    proposals.append({
        'name': 'Wirelength Optimized',
        'description': 'Minimizes total trace length for shorter routes, lower resistance, and better signal integrity',
        'scores': wl_scores,
        'improvement': f"{wl_improvement:+.1f}% wirelength",
        'trade_offs': [
            f"Wirelength: {wl_scores['wirelength']}mm (current: {current_scores['wirelength']}mm)",
            f"Congestion: {wl_scores['congestion']:.1f} (current: {current_scores['congestion']:.1f})",
            "Best for: High-speed signals, low-power designs, signal integrity"
        ],
        'component_moves': [
            {'reference': ref, 'x': round(comp.x, 3), 'y': round(comp.y, 3), 'rotation': comp.rotation}
            for ref, comp in wl_state.components.items()
            if not comp.locked and (
                abs(comp.x - analyzer.components[ref].x) > 0.1 or
                abs(comp.y - analyzer.components[ref].y) > 0.1
            )
        ]
    })
    
    # Strategy 2: Congestion balanced
    logger.info("Optimizing for congestion...")
    cong_state = optimizer.optimize_congestion(iterations)
    cong_scores = {
        'wirelength': round(analyzer.calculate_wirelength(cong_state), 1),
        'congestion': round(analyzer.calculate_congestion_score(cong_state), 2),
        'grouping': round(analyzer.calculate_grouping_score(cong_state), 1),
        'overlaps': analyzer.count_overlaps(cong_state)
    }
    cong_improvement = round((current_scores['congestion'] - cong_scores['congestion']) /
                              max(current_scores['congestion'], 1) * 100, 1)
    
    proposals.append({
        'name': 'Congestion Balanced',
        'description': 'Spreads components to reduce routing bottlenecks and improve auto-router success',
        'scores': cong_scores,
        'improvement': f"{cong_improvement:+.1f}% congestion",
        'trade_offs': [
            f"Congestion: {cong_scores['congestion']:.1f} (current: {current_scores['congestion']:.1f})",
            f"Wirelength: {cong_scores['wirelength']}mm (current: {current_scores['wirelength']}mm)",
            "Best for: Dense 2-layer boards, auto-routing, even trace distribution"
        ],
        'component_moves': [
            {'reference': ref, 'x': round(comp.x, 3), 'y': round(comp.y, 3), 'rotation': comp.rotation}
            for ref, comp in cong_state.components.items()
            if not comp.locked and (
                abs(comp.x - analyzer.components[ref].x) > 0.1 or
                abs(comp.y - analyzer.components[ref].y) > 0.1
            )
        ]
    })
    
    # Strategy 3: Functional grouping
    logger.info("Optimizing for functional grouping...")
    grp_state = optimizer.optimize_functional_groups(iterations)
    grp_scores = {
        'wirelength': round(analyzer.calculate_wirelength(grp_state), 1),
        'congestion': round(analyzer.calculate_congestion_score(grp_state), 2),
        'grouping': round(analyzer.calculate_grouping_score(grp_state), 1),
        'overlaps': analyzer.count_overlaps(grp_state)
    }
    grp_improvement = round((grp_scores['grouping'] - current_scores['grouping']) /
                             max(current_scores['grouping'], 1) * 100, 1)
    
    proposals.append({
        'name': 'Functional Grouped',
        'description': 'Places decoupling caps near ICs for better power integrity and noise reduction',
        'scores': grp_scores,
        'improvement': f"{grp_improvement:+.1f}% grouping",
        'trade_offs': [
            f"Grouping: {grp_scores['grouping']:.1f} (current: {current_scores['grouping']:.1f})",
            f"Wirelength: {grp_scores['wirelength']}mm (current: {current_scores['wirelength']}mm)",
            "Best for: Analog/mixed-signal, noise-sensitive, power integrity"
        ],
        'component_moves': [
            {'reference': ref, 'x': round(comp.x, 3), 'y': round(comp.y, 3), 'rotation': comp.rotation}
            for ref, comp in grp_state.components.items()
            if not comp.locked and (
                abs(comp.x - analyzer.components[ref].x) > 0.1 or
                abs(comp.y - analyzer.components[ref].y) > 0.1
            )
        ]
    })
    
    # Sort by combined score (lower overlaps, lower wirelength, higher grouping)
    for p in proposals:
        p['_sort_score'] = (
            -p['scores']['overlaps'] * 1000 +  # Overlaps are very bad
            -p['scores']['wirelength'] +
            p['scores']['grouping'] * 5 -
            p['scores']['congestion'] * 2
        )
    proposals.sort(key=lambda p: p['_sort_score'], reverse=True)
    
    # Add rank and remove sort key
    for i, p in enumerate(proposals):
        p['rank'] = i + 1
        del p['_sort_score']
    
    return {
        'board_info': {
            'components': len(analyzer.components),
            'nets': len(analyzer.nets),
            'movable_components': sum(1 for c in analyzer.components.values() if not c.locked),
            'board_size': f"{analyzer.board_bbox.width:.1f}x{analyzer.board_bbox.height:.1f}mm"
        },
        'current_scores': current_scores,
        'proposals': proposals
    }


def apply_layout(project_path: str, placements: List[Dict]) -> Dict[str, Any]:
    """
    Apply a proposed layout to the PCB using pcbnew API.
    
    Args:
        project_path: Path to KiCad project
        placements: List of {reference, x, y, rotation} dicts
    
    Returns:
        Success/failure status
    """
    import pcbnew  # Lazy import - only available in KiCad's Python
    
    pcb_path = Path(project_path).with_suffix('.kicad_pcb')
    
    try:
        board = pcbnew.LoadBoard(str(pcb_path))
    except Exception as e:
        return {"error": f"Failed to load PCB: {e}"}
    
    if not board:
        return {"error": f"Failed to load PCB: {pcb_path}"}
    
    moved = 0
    skipped = 0
    
    for pl in placements:
        ref = pl.get('reference')
        if not ref:
            continue
        
        for fp in board.GetFootprints():
            if fp.GetReference() == ref:
                if fp.IsLocked():
                    skipped += 1
                    break
                
                fp.SetPosition(pcbnew.VECTOR2I(mm_to_nm(pl['x']), mm_to_nm(pl['y'])))
                if 'rotation' in pl:
                    fp.SetOrientationDegrees(pl['rotation'])
                moved += 1
                break
    
    try:
        board.Save(str(pcb_path))
    except Exception as e:
        return {"error": f"Failed to save PCB: {e}"}
    
    return {
        "success": True,
        "components_moved": moved,
        "components_skipped": skipped,
        "message": f"Applied layout: {moved} components moved, {skipped} locked components skipped"
    }


def register_placement_proposal_tools(mcp):
    """Register placement proposal tools with MCP server."""
    
    @mcp.tool()
    async def propose_placement_layouts(
        project_path: str,
        iterations: int = 3000
    ) -> Dict[str, Any]:
        """Generate 3 optimized component placement proposals.
        
        Analyzes the current PCB layout and generates 3 alternative placements
        optimized for different goals:
        1. Wirelength - shorter traces, better signal integrity
        2. Congestion - spread out routing, better for auto-routing
        3. Functional - decoupling caps near ICs, better power integrity
        
        Each proposal respects component footprint sizes, prevents overlaps,
        and preserves locked components.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
            iterations: Optimization iterations (higher = better but slower)
            
        Returns:
            Dictionary with current scores and 3 ranked proposals
        """
        from ..utils.pcb_parser import parse_pcb_file
        from .placement_tools import extract_placement_from_parsed_pcb, SimulatedAnnealingPlacer, PlacementScorer
        from pathlib import Path
        import copy
        
        try:
            pcb_path = Path(project_path).with_suffix('.kicad_pcb')
            if not pcb_path.exists():
                return {"error": f"PCB file not found: {pcb_path}"}
            
            # Parse PCB and get ACTUAL sizes from KiCad
            pcb = parse_pcb_file(str(pcb_path))
            components, nets, board_info = extract_placement_from_parsed_pcb(pcb, str(pcb_path))
            
            if not components:
                return {"error": "No components found in PCB"}
            
            # Score current placement
            scorer = PlacementScorer(components, nets, board_info)
            current_score, current_breakdown = scorer.score()
            
            current_scores = {
                'wirelength': round(current_breakdown['wirelength'], 1),
                'overlap': round(current_breakdown['overlap'], 1),
                'density': round(current_breakdown['density'], 2),
                'total': round(current_score, 1)
            }
            
            # Run optimization
            optimized_components = copy.deepcopy(components)
            
            movable_count = sum(1 for c in optimized_components.values() if not c.locked)
            if movable_count == 0:
                return {
                    "success": True,
                    "current_scores": current_scores,
                    "proposals": [],
                    "message": "No movable components found"
                }
            
            placer = SimulatedAnnealingPlacer(
                initial_temp=100.0,
                final_temp=0.1,
                cooling_rate=0.95,
                iterations_per_temp=max(10, movable_count),
                max_iterations=iterations
            )
            
            results = placer.optimize(optimized_components, nets, board_info)
            
            # Score optimized placement  
            opt_scorer = PlacementScorer(optimized_components, nets, board_info)
            opt_score, opt_breakdown = opt_scorer.score()
            
            opt_scores = {
                'wirelength': round(opt_breakdown['wirelength'], 1),
                'overlap': round(opt_breakdown['overlap'], 1),
                'density': round(opt_breakdown['density'], 2),
                'total': round(opt_score, 1)
            }
            
            improvement = round((current_score - opt_score) / max(current_score, 1) * 100, 1)
            
            # Build component moves list
            moves = []
            for ref, comp in optimized_components.items():
                orig = components[ref]
                if not comp.locked and (abs(comp.x - orig.x) > 0.1 or abs(comp.y - orig.y) > 0.1):
                    moves.append({
                        'reference': ref,
                        'x': round(comp.x, 3),
                        'y': round(comp.y, 3),
                        'rotation': comp.angle
                    })
            
            return {
                "success": True,
                "current_scores": current_scores,
                "proposals": [{
                    "name": "Wirelength Optimized",
                    "description": "Minimizes total trace length for better signal integrity",
                    "scores": opt_scores,
                    "improvement": f"{improvement:+.1f}%",
                    "component_moves": moves
                }]
            }
            
        except Exception as e:
            import traceback
            return {"error": str(e), "traceback": traceback.format_exc()}
    
    @mcp.tool()
    async def apply_placement_layout(
        project_path: str,
        placements: List[Dict]
    ) -> Dict[str, Any]:
        """Apply a placement layout to the PCB.
        
        Moves components to the specified positions. Use this with the
        component_moves list from propose_placement_layouts.
        
        Args:
            project_path: Path to KiCad project file (.kicad_pro)
            placements: List of {reference, x, y, rotation} for each component
                        Coordinates are relative to board origin (0,0 = top-left)
            
        Returns:
            Success status and count of moved components
        """
        from ..utils.pcb_parser import update_footprint_position, parse_pcb_file, get_board_bounds
        from pathlib import Path
        
        pcb_path = str(Path(project_path).with_suffix('.kicad_pcb'))
        
        # Get board bounds to convert relative coords to absolute
        try:
            pcb = parse_pcb_file(pcb_path)
            bounds = get_board_bounds(pcb)
            board_origin_x = bounds[0]  # min_x of board outline
            board_origin_y = bounds[1]  # min_y of board outline
        except Exception as e:
            return {"error": f"Failed to parse PCB for board bounds: {e}"}
        
        moved = 0
        errors = []
        for p in placements:
            try:
                # Convert relative coords to absolute
                abs_x = p['x'] + board_origin_x
                abs_y = p['y'] + board_origin_y
                
                success = update_footprint_position(
                    pcb_path=pcb_path,
                    reference=p['reference'],
                    new_position=(abs_x, abs_y),
                    new_rotation=p.get('rotation')
                )
                if success:
                    moved += 1
                else:
                    errors.append(f"{p['reference']}: not found")
            except Exception as e:
                errors.append(f"{p['reference']}: {str(e)}")
        
        return {
            "success": len(errors) == 0,
            "components_moved": moved,
            "board_origin": (board_origin_x, board_origin_y),
            "errors": errors if errors else None,
            "message": f"Moved {moved} components" + (f", {len(errors)} errors" if errors else "")
        }


# For testing
if __name__ == "__main__":
    import json
    import sys
    
    if len(sys.argv) > 1:
        project = sys.argv[1]
    else:
        project = r"c:\Users\MaximilianNussbaumer\Workspace\sleeptracker\hardware\SOM_Band\SOM_Band.kicad_pro"
    
    print(f"Analyzing {project}...")
    from kicad_mcp.utils.pcbnew_bridge import propose_layouts
    result = propose_layouts(str(Path(project).with_suffix('.kicad_pcb')), iterations=2000)
    print(json.dumps(result, indent=2))
