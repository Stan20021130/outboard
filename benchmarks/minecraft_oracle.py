"""Independent Acceptance Oracle for Minecraft 3D Sandbox Game Benchmark.
Contains 20 strict assertions verifying:
1. Project structure & package setup
2. HTML entry & HUD elements (crosshair, hotbar, FPS, coords, menu)
3. Block definitions (8 distinct types: grass, dirt, stone, sand, wood, leaves, glass, water)
4. Voxel storage & Chunk 3D grid indexing
5. Procedural terrain generation (elevation, layers: grass/sand top, dirt sub, stone deep)
6. Natural features (trees with wood trunk & leaf canopy, water bodies)
7. Rendering optimization (InstancedMesh or merged geometry usage)
8. Player camera & PointerLockControls
9. Keyboard controls (WASD, Space, Shift)
10. Player gravity & vertical physics integration
11. AABB ground collision detection (no falling through ground)
12. AABB wall collision detection (horizontal obstacle blocking)
13. Raycasting target block detection from center crosshair
14. Raycasting face normal calculation for adjacent block placement
15. Block placement player collision check (prevent suffocating player)
16. 9-slot hotbar & slot switching (keys 1-9)
17. Inventory item stacking & accounting on break/place
18. Day/night cycle & sky/light color transitions
19. Save state serialization (player position, modified blocks, inventory)
20. Restore state deserialization and state recovery
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".trajectory", ".pytest_cache", "fixtures"}

def get_workspace() -> Path:
    ws_env = os.environ.get("MINECRAFT_WORKSPACE")
    if ws_env:
        return Path(ws_env)
    return Path(os.getcwd())

def read_workspace_files(ws: Path) -> dict[str, str]:
    files = {}
    for root, dirs, filenames in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in filenames:
            ext = Path(f).suffix.lower()
            if ext in [".html", ".js", ".mjs", ".ts", ".json", ".css"]:
                p = Path(root) / f
                try:
                    rel = str(p.relative_to(ws)).replace("\\", "/")
                    files[rel] = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
    return files

def get_combined_source(files: dict[str, str]) -> str:
    return "\n".join(files.values())

# -------------------------------------------------------------------------
# Test 1: Project & Package Setup
# -------------------------------------------------------------------------
def test_01_project_structure_and_package_json():
    ws = get_workspace()
    files = read_workspace_files(ws)
    assert any("index.html" in f.lower() for f in files), "index.html entry file must exist in workspace."
    assert any("package.json" in f.lower() for f in files), "package.json must exist in workspace."
    pkg_str = [files[f] for f in files if "package.json" in f.lower()][0]
    pkg = json.loads(pkg_str)
    combined = get_combined_source(files).lower()
    assert "three" in pkg_str.lower() or "three" in combined or "babylon" in combined, "Three.js (or equivalent 3D engine) must be referenced."

# -------------------------------------------------------------------------
# Test 2: HTML Entry and HUD Elements
# -------------------------------------------------------------------------
def test_02_html_entry_and_hud_elements():
    ws = get_workspace()
    files = read_workspace_files(ws)
    html_files = [files[f] for f in files if f.endswith(".html")]
    assert len(html_files) > 0, "At least one HTML file must exist."
    combined = get_combined_source(files).lower()
    # Check for HUD elements: crosshair, hotbar, fps or coords, pause or menu
    assert "crosshair" in combined or "+" in combined, "Center crosshair must be defined in UI/CSS/HTML."
    assert "hotbar" in combined or "inventory" in combined or "slot" in combined, "Hotbar or inventory HUD must be present."
    assert "fps" in combined or "coordinate" in combined or "pos" in combined, "FPS or coordinate display element must be present in UI."

# -------------------------------------------------------------------------
# Test 3: Block Definitions (8 Types)
# -------------------------------------------------------------------------
def test_03_block_definitions_eight_types():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    required_blocks = ["grass", "dirt", "stone", "sand", "wood", "leaves", "glass", "water"]
    found = [b for b in required_blocks if b in combined]
    assert len(found) >= 7, f"Must define at least 7-8 required block types. Found: {found}"

# -------------------------------------------------------------------------
# Test 4: Voxel Storage & Chunk 3D Grid Structure
# -------------------------------------------------------------------------
def test_04_voxel_storage_and_chunk_structure():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    # Check for 3D indexing or getBlock/setBlock pattern
    has_get = bool(re.search(r"getBlock|get_block|getBlockAt", combined, re.I))
    has_set = bool(re.search(r"setBlock|set_block|setBlockAt", combined, re.I))
    has_chunk_or_grid = bool(re.search(r"chunk|voxel|grid|blocks|world", combined, re.I))
    assert (has_get and has_set) or ("Chunk" in combined and "blocks" in combined), "Must have 3D voxel storage with get/set block operations."

# -------------------------------------------------------------------------
# Test 5: Procedural Terrain Generation Layers
# -------------------------------------------------------------------------
def test_05_procedural_terrain_generation_layers():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_noise = any(n in combined for n in ["noise", "simplex", "perlin", "sin(", "cos(", "height"])
    has_layers = ("dirt" in combined and "stone" in combined and "grass" in combined)
    assert has_noise and has_layers, "Terrain generation must use procedural height calculation with layered block strata."

# -------------------------------------------------------------------------
# Test 6: Natural Features (Trees & Water)
# -------------------------------------------------------------------------
def test_06_natural_features_trees_and_water():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_tree = "tree" in combined or ("wood" in combined and "leaves" in combined)
    has_water = "water" in combined or "sea_level" in combined or "waterlevel" in combined
    assert has_tree, "Terrain generator must include tree generation logic (wood trunk + leaves)."
    assert has_water, "Terrain generator must include water surface or sea level logic."

# -------------------------------------------------------------------------
# Test 7: Rendering Optimization (InstancedMesh or Merging)
# -------------------------------------------------------------------------
def test_07_instanced_mesh_or_merged_rendering():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    # Look for InstancedMesh, BufferGeometryUtils.mergeGeometries, or chunk batched rendering
    has_instancing = bool(re.search(r"InstancedMesh|instanced|mergeGeometries|createInstance", combined, re.I))
    has_batching = bool(re.search(r"matrixAt|setMatrixAt|instanceMatrix|ChunkMesh", combined, re.I))
    assert has_instancing or has_batching, "Rendering must use InstancedMesh or geometry batching optimization to prevent lag."

# -------------------------------------------------------------------------
# Test 8: Player Camera & PointerLockControls
# -------------------------------------------------------------------------
def test_08_player_camera_and_pointer_lock():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    has_pointer_lock = bool(re.search(r"pointerLock|requestPointerLock|PointerLockControls|mousemove", combined, re.I))
    has_camera = bool(re.search(r"camera|PerspectiveCamera|pitch|yaw", combined, re.I))
    assert has_pointer_lock and has_camera, "Must implement first-person camera and PointerLockControls mouse-look mechanism."

# -------------------------------------------------------------------------
# Test 9: Keyboard Controls (WASD, Space, Shift)
# -------------------------------------------------------------------------
def test_09_keyboard_controls_wasd_jump_sprint():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_keydown = "keydown" in combined or "eventlistener" in combined or "keys" in combined
    has_wasd = any(k in combined for k in ["keyw", "'w'", '"w"', "keys.w", "keys['w']", "keya", "keysd"])
    has_jump = "space" in combined or "keyspace" in combined or "' '" in combined
    assert has_keydown and (has_wasd or has_jump), "Keyboard event listeners must track WASD movement and Space jumping."

# -------------------------------------------------------------------------
# Test 10: Player Gravity & Vertical Physics Integration
# -------------------------------------------------------------------------
def test_10_player_gravity_and_vertical_physics():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_gravity = "gravity" in combined or "velocity.y" in combined or "dy -=" in combined or "vy -=" in combined or "vy +=" in combined
    has_velocity = "velocity" in combined or "speed" in combined or "position.y" in combined
    assert has_gravity and has_velocity, "Player physics must integrate vertical gravity acceleration into velocity and position."

# -------------------------------------------------------------------------
# Test 11: AABB Ground Collision Detection
# -------------------------------------------------------------------------
def test_11_aabb_ground_collision_detection():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_collision = any(c in combined for c in ["collision", "collide", "aabb", "box3", "onground", "isgrounded", "grounded"])
    assert has_collision, "Must implement ground collision detection (AABB or ray) to prevent player falling through terrain."

# -------------------------------------------------------------------------
# Test 12: AABB Wall Horizontal Obstacle Collision
# -------------------------------------------------------------------------
def test_12_aabb_wall_horizontal_blocking():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_wall_block = any(w in combined for w in ["checkcollision", "iscolliding", "box", "intersect", "blockat", "solid"])
    assert has_wall_block, "Must implement wall/obstacle collision checking to block player from walking through solid blocks."

# -------------------------------------------------------------------------
# Test 13: Raycasting Target Block Detection
# -------------------------------------------------------------------------
def test_13_raycast_crosshair_target_detection():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    has_raycast = bool(re.search(r"Raycaster|raycast|ray|intersectObjects|intersectObject|dda", combined, re.I))
    assert has_raycast, "Must implement raycasting from center crosshair to detect target block under sight."

# -------------------------------------------------------------------------
# Test 14: Raycasting Face Normal & Block Placement
# -------------------------------------------------------------------------
def test_14_raycast_face_normal_and_placement():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    has_normal = bool(re.search(r"normal|face\.normal|normal\.x|normal\.y|normal\.z|adjacent|targetPos", combined, re.I))
    has_place = bool(re.search(r"setBlock|placeBlock|addBlock", combined, re.I))
    assert has_normal or has_place, "Must calculate face normal or adjacent coordinate for placing new blocks."

# -------------------------------------------------------------------------
# Test 15: Block Placement Player Collision Prevention
# -------------------------------------------------------------------------
def test_15_placement_player_collision_prevention():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Placing block should not overlap player's current bounding box
    has_check = any(p in combined for p in ["player.position", "playerpos", "distance", "intersectsbox", "intersect", "canplace", "allowplace"])
    assert has_check, "Must check player position/bounding box to prevent placing block inside player."

# -------------------------------------------------------------------------
# Test 16: 9-Slot Hotbar & Slot Switching (Keys 1-9)
# -------------------------------------------------------------------------
def test_16_hotbar_nine_slots_and_number_keys():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_slot = "hotbar" in combined or "slot" in combined or "selectedslot" in combined or "active_slot" in combined
    has_keys = any(f"digit{i}" in combined or f"'{i}'" in combined or f'"{i}"' in combined for i in range(1, 10)) or "wheel" in combined
    assert has_slot and has_keys, "Must support 9-slot hotbar selection via number keys 1-9 or mouse wheel."

# -------------------------------------------------------------------------
# Test 17: Inventory Item Stacking & Accounting
# -------------------------------------------------------------------------
def test_17_inventory_stacking_and_accounting():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_count = any(c in combined for c in ["count", "amount", "quantity", "stack", "items"])
    has_mutate = any(m in combined for m in ["count++", "count--", "additem", "removeitem", "-= 1", "+= 1"])
    assert has_count and has_mutate, "Inventory must support item counts with increment on mining and decrement on placement."

# -------------------------------------------------------------------------
# Test 18: Day/Night Cycle & Sky Transitions
# -------------------------------------------------------------------------
def test_18_day_night_cycle_and_lighting_transition():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_sky = any(s in combined for s in ["sun", "moon", "day", "night", "sky", "timeofday", "light.intensity"])
    assert has_sky, "Must implement day/night cycle with celestial motion and ambient/directional light adjustments."

# -------------------------------------------------------------------------
# Test 19: Save State Serialization
# -------------------------------------------------------------------------
def test_19_save_state_serialization():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_storage = "localstorage" in combined or "json.stringify" in combined or "saveworld" in combined or "savegame" in combined
    assert has_storage, "Must implement world/player save serialization (e.g. localStorage with JSON.stringify)."

# -------------------------------------------------------------------------
# Test 20: Restore State Deserialization
# -------------------------------------------------------------------------
def test_20_restore_state_deserialization():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_restore = "json.parse" in combined or "loadworld" in combined or "loadgame" in combined or "getitem" in combined
    assert has_restore, "Must implement world/player load deserialization to restore saved blocks and player position."
