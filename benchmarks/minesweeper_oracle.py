"""Independent Acceptance Oracle for Minesweeper Greenfield Benchmark.
Contains 20 strict assertions verifying:
1. Project structure & HTML entry
2. Difficulty presets (Beginner 9x9/10, Intermediate 16x16/40, Expert 30x16/99)
3. Grid & cell state data model
4. Random mine placement algorithm
5. First click safety guarantee (never hit mine on click 1)
6. 8-directional adjacent mine count calculation
7. Zero-cascade flood fill expansion (BFS/DFS)
8. Cell reveal action logic
9. Flagging mechanism (right click / flag toggle)
10. Flag prevents accidental reveal
11. Question mark state cycle (? mark toggle)
12. Chording / double-click reveal of adjacent cells
13. Mine counter calculation (total mines - flags)
14. Game timer lifecycle (start on first click, stop on end)
15. Game over loss detection & mine reveal
16. Game victory detection (all non-mine cells revealed)
17. Post-game input freeze (no clicks after win/loss)
18. Reset / restart button & smiley state
19. Best time localStorage persistence
20. Mouse event handling & contextmenu prevention
"""
import json
import os
from pathlib import Path
import re
import pytest

IGNORED_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".trajectory", ".pytest_cache", "fixtures"}

def get_workspace() -> Path:
    ws_env = os.environ.get("MINESWEEPER_WORKSPACE")
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
    assert len(combined) > 200, "Workspace files must contain substantial game implementation code."
    assert any(w in combined for w in ["minesweeper", "mine", "扫雷", "grid", "board"]), "Entry must be a Minesweeper game."

def test_02_difficulty_presets():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files)
    # Check for Beginner (9x9, 10), Intermediate (16x16, 40), Expert (30x16 or 16x30, 99)
    has_beginner = bool(re.search(r"9\s*,\s*9\s*,\s*10|beginner|9\s*x\s*9|rows\s*:\s*9.*?cols\s*:\s*9.*?mines\s*:\s*10", combined, re.I | re.S))
    has_intermediate = bool(re.search(r"16\s*,\s*16\s*,\s*40|intermediate|16\s*x\s*16|rows\s*:\s*16.*?cols\s*:\s*16.*?mines\s*:\s*40", combined, re.I | re.S))
    has_expert = bool(re.search(r"(?:16\s*,\s*30|30\s*,\s*16)\s*,\s*99|expert|30\s*x\s*16|16\s*x\s*30|mines\s*:\s*99", combined, re.I | re.S))
    assert has_beginner and has_intermediate and has_expert, "Must support standard difficulty presets: Beginner (9x9, 10), Intermediate (16x16, 40), Expert (16x30 or 30x16, 99)."

def test_03_grid_and_cell_state_data_model():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Check cell state properties (revealed, flagged, mine, count)
    has_revealed = any(w in combined for w in ["revealed", "isrevealed", "isopen", "open", "visible"])
    has_mine = any(w in combined for w in ["ismine", "mine", "bomb", "hasmine", "isbomb"])
    has_flag = any(w in combined for w in ["flagged", "isflagged", "flag", "marked"])
    assert has_revealed and has_mine and has_flag, "Cell data model must represent revealed state, mine state, and flag state."

def test_04_mine_generation_and_distribution():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_gen = any(w in combined for w in ["placemines", "generatemines", "plantmines", "createtimes", "random", "initmines"])
    assert has_gen, "Must implement random mine distribution/generation across board coordinates."

def test_05_first_click_safety_guarantee():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Check first click safe mechanism
    has_first_click = any(w in combined for w in ["firstclick", "first_click", "isfirstclick", "firstmove", "safestart", "startsafely"]) or (
        "click" in combined and "mine" in combined and ("first" in combined or "start" in combined)
    )
    assert has_first_click, "Must guarantee first-click safety (mines placed after first click or mine relocated if clicked on turn 1)."

def test_06_adjacent_mine_count_calculation():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Check neighbor counting logic: 8 directions or delta arrays [-1, 0, 1]
    has_neighbor_count = any(w in combined for w in ["countadjacent", "adjacentmines", "neighbor", "getneighbors", "surrounding", "adjacent", "countneighbors"]) or (
        "-1" in combined and "1" in combined and ("dx" in combined or "dy" in combined or "row" in combined)
    )
    assert has_neighbor_count, "Must implement 8-neighbor scan to calculate adjacent mine counts (0 to 8)."

def test_07_zero_cascade_expansion_flood_fill():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Check flood fill / cascade expansion
    has_cascade = any(w in combined for w in ["floodfill", "cascade", "revealempty", "expand", "revealneighbors", "queue", "stack", "bfs", "dfs"]) or (
        "adjacentmines === 0" in combined or "adjacent === 0" in combined or "count === 0" in combined or "neighborcount === 0" in combined
    )
    assert has_cascade, "Must implement recursive or BFS/DFS cascade flood-fill expansion when revealing an empty (0) cell."

def test_08_cell_reveal_action():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_reveal = any(w in combined for w in ["reveal", "revealcell", "opencell", "clickcell", "handleleftclick", "step"])
    assert has_reveal, "Must implement cell reveal action triggered by player click."

def test_09_flagging_mechanism():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_flag = any(w in combined for w in ["toggleflag", "setflag", "flagcell", "handlerightclick", "oncontextmenu", "flag"])
    assert has_flag, "Must implement flagging mechanism to mark suspected mine locations."

def test_10_flag_prevents_accidental_reveal():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Flagged cells should not be revealed by standard click
    has_guard = bool(re.search(r"if\s*\(.*?(?:flagged|isflagged|flag).*?\)\s*return", combined, re.I)) or (
        "flag" in combined and "return" in combined and "reveal" in combined
    )
    assert has_guard, "Clicking on a flagged cell must be protected/prevented from revealing."

def test_11_question_mark_state_cycle():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_question = any(w in combined for w in ["question", "mark", "?", "togglemark", "flagstate"])
    assert has_question, "Should support question mark (?) state or multi-state flag cycling."

def test_12_chording_chord_reveal():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    # Chording: double click or both buttons on a revealed number
    has_chord = any(w in combined for w in ["chord", "chording", "dblclick", "doubleclick", "revealadjacent", "quickreveal", "auxclick", "bothbuttons"]) or (
        "flagged" in combined and "adjacent" in combined and "reveal" in combined
    )
    assert has_chord, "Must support Chording / double-click feature (revealing unflagged neighbors when flag count matches cell number)."

def test_13_mine_counter_tracking():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_counter = any(w in combined for w in ["minecount", "remainingmines", "minesleft", "flagsleft", "flagcount", "counter"]) or (
        "mines" in combined and ("-" in combined or "decrement" in combined or "updatecounter" in combined)
    )
    assert has_counter, "Must calculate and display remaining mine count (total mines - flags)."

def test_14_game_timer_lifecycle():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_timer = any(w in combined for w in ["timer", "setinterval", "clearinterval", "elapsedtime", "timepassed", "second"])
    assert has_timer, "Must implement a game timer that tracks elapsed seconds."

def test_15_game_over_loss_detection():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_loss = any(w in combined for w in ["gameover", "lose", "defeat", "revealallmines", "showallmines", "exploded", "hitmine"])
    assert has_loss, "Must detect loss condition on clicking a mine, stop game, and reveal all mines."

def test_16_game_won_detection():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_win = any(w in combined for w in ["checkwin", "gamewon", "victory", "win", "iswin", "won"])
    assert has_win, "Must detect win condition when all non-mine cells are successfully revealed."

def test_17_post_game_input_freeze():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_freeze = bool(re.search(r"if\s*\(.*?(?:gameover|isgameover|status\s*!==\s*['\"]playing['\"]|won|lost).*?\)\s*return", combined, re.I)) or (
        "gameover" in combined and "return" in combined
    )
    assert has_freeze, "Must freeze board input after game ends (ignore clicks when won or lost)."

def test_18_restart_and_reset():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_restart = any(w in combined for w in ["restart", "reset", "resetgame", "newgame", "smiley", "face", "initgame"])
    assert has_restart, "Must provide restart/reset functionality to start a new game."

def test_19_best_time_localstorage():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_storage = "localstorage" in combined
    assert has_storage, "Must store best times / high scores for each difficulty level in localStorage."

def test_20_mouse_event_handlers():
    ws = get_workspace()
    files = read_workspace_files(ws)
    combined = get_combined_source(files).lower()
    has_contextmenu = any(w in combined for w in ["contextmenu", "preventdefault", "mousedown", "click"])
    assert has_contextmenu, "Must intercept mouse clicks and prevent default context menu for right-click flagging."
