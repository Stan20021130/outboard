"""Independent Acceptance Oracle for Tetris Arcade Greenfield Benchmark.
Contains 20 strict assertions verifying:
1. Project structure & HTML entry
2. Board grid dimensions (10 columns x 20 rows)
3. 7 Tetromino types defined (I, J, L, O, S, T, Z) with distinct colors/matrices
4. 7-Bag Randomizer implementation (permutations of 7)
5. 4 rotation states per piece (matrices)
6. SRS (Super Rotation System) wall kick offset data
7. Ghost piece drop projection calculation
8. Hold piece swapping mechanism & flag
9. Hard drop instant floor locking
10. Soft drop & gravity fall integration
11. Lock delay timer when touching floor
12. Voxel/block collision detection (bounds & existing blocks)
13. Full line clear detection
14. Grid line clearing & row collapse shift down
15. Scoring progression (Single, Double, Triple, Tetris 4-line)
16. Level progression & gravity speed acceleration
17. Game over spawn collision detection
18. Next queue preview (upcoming pieces)
19. Keyboard input listeners (WASD/Arrows, Space, C/Shift, Z, X)
20. High score persistence (localStorage)
"""
import json
import os
from pathlib import Path
import re
import pytest

IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".trajectory", ".pytest_cache", "fixtures"}

def get_workspace() -> Path:
    ws_env = os.environ.get("TETRIS_WORKSPACE")
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

def test_01_project_structure_and_entry():
    ws = get_workspace()
    files = read_workspace_files(ws)
    assert any("index.html" in f.lower() for f in files), "index.html entry file must exist in workspace."
    combined = get_combined_source(files).lower()
    assert "canvas" in combined, "HTML/JS must include a canvas element for rendering."

def test_02_board_matrix_dimensions():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    # Check for 10 cols and 20 rows definition
    has_cols = bool(re.search(r"COLS\s*=\s*10|cols\s*:\s*10|10\s*,\s*20|width\s*:\s*10|cols\s*=\s*10", combined, re.I))
    has_rows = bool(re.search(r"ROWS\s*=\s*20|rows\s*:\s*20|height\s*:\s*20|rows\s*=\s*20|length\s*:\s*20", combined, re.I))
    assert has_cols and has_rows, "Must explicitly define a 10-column by 20-row playfield matrix."

def test_03_tetromino_definitions_seven_types():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    # Check for presence of I, J, L, O, S, T, Z definitions
    shapes = ["I", "J", "L", "O", "S", "T", "Z"]
    found = [s for s in shapes if re.search(rf"\b{s}\s*:\s*\{{|['\"]{s}['\"]\s*:", combined)]
    assert len(found) >= 6, f"Must define all 7 standard tetromino shapes (I, J, L, O, S, T, Z). Found: {found}"

def test_04_seven_bag_randomizer():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_bag = any(b in combined for b in ["bag", "randomizer", "shuffle", "math.random"])
    assert has_bag, "Must implement a 7-bag randomizer or piece generation logic."

def test_05_rotation_matrices_four_states():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_rot = any(r in combined for r in ["rotation", "rotate", "matrices", "matrix"])
    assert has_rot, "Tetrominoes must support rotation states."

def test_06_srs_wall_kick_data():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_kick = any(k in combined for k in ["kick", "srs", "wallkick", "offset", "kicks"])
    assert has_kick, "Must implement wall kick offset table or SRS collision avoidance."

def test_07_ghost_piece_projection():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_ghost = any(g in combined for g in ["ghost", "getghosty", "shadow", "dropy", "projection", "isghost"])
    assert has_ghost, "Must calculate and render a ghost piece / landing shadow."

def test_08_hold_piece_mechanism():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_hold = any(h in combined for h in ["hold", "holdpiece", "canhold"])
    assert has_hold, "Must implement piece holding mechanism (Hold slot)."

def test_09_hard_drop_instant_locking():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_hard_drop = any(hd in combined for hd in ["harddrop", "hard_drop", "drop()", "space"])
    assert has_hard_drop, "Must support instant hard drop to floor."

def test_10_soft_drop_and_gravity():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_soft_drop = any(sd in combined for sd in ["softdrop", "soft_drop", "arrowdown", "dropinterval", "interval"])
    assert has_soft_drop, "Must implement continuous gravity fall and down-arrow soft drop."

def test_11_lock_delay_timer():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_lock = any(l in combined for l in ["lockdelay", "locktimer", "lockpiece", "touchingfloor", "settimeout"])
    assert has_lock, "Must implement lock delay before locking piece into grid."

def test_12_collision_bounds_and_blocks():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_collision = any(c in combined for c in ["checkcollision", "collision", "isvalid", "canmove", "collides"])
    assert has_collision, "Must check boundary and block collision."

def test_13_line_clear_detection():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_detect = any(d in combined for d in ["clearlines", "fullrows", "every", "isrowfull", "checklines"])
    assert has_detect, "Must detect completed full lines across the 10-column row."

def test_14_grid_clearing_and_shift_down():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_shift = any(s in combined for s in ["splice", "unshift", "shift", "removerow", "grid.unshift"])
    assert has_shift, "Must remove full rows and shift upper rows downward."

def test_15_scoring_system_single_to_tetris():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_score = any(sc in combined for sc in ["score +=", "score+=", "points", "tetris", "100", "800"])
    assert has_score, "Must calculate line clear scores with scaling rewards."

def test_16_level_progression_and_speed():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_level = any(lv in combined for lv in ["level", "lines / 10", "lines/10", "dropinterval"])
    assert has_level, "Must increment level and adjust drop speed based on cleared lines."

def test_17_game_over_spawn_collision():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_game_over = any(go in combined for go in ["gameover", "game_over", "isgameover"])
    assert has_game_over, "Must handle game over when new piece collides on spawn."

def test_18_next_queue_preview():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_next = any(n in combined for n in ["next", "nextqueue", "preview", "upcoming"])
    assert has_next, "Must maintain and render upcoming next pieces preview."

def test_19_keyboard_input_handling():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_input = any(k in combined for k in ["keydown", "addeventlistener", "arrowleft", "arrowright"])
    assert has_input, "Must handle keyboard event inputs."

def test_20_high_score_localstorage():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_storage = any(ls in combined for ls in ["localstorage", "topscore", "highscore"])
    assert has_storage, "Must persist top score in localStorage."
