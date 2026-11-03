/**
 * Minesweeper Retro — Engine & Controller
 * ------------------------------------------------------------------
 * A complete, from-scratch implementation of the classic game:
 *
 *   • three standard presets (9x9/10, 16x16/40, 16x30/99)
 *   • guaranteed-safe first click (mines are generated lazily)
 *   • exact 8-neighbour mine counting with classic digit colours
 *   • zero-cascade flood fill (BFS) for empty openings
 *   • flag / question-mark cycling with flag protection
 *   • chording (double click, both-buttons, middle click)
 *   • mine counter + 0..999 timer
 *   • win & loss resolution with post-game input freeze
 *   • classic smiley button states (🙂 😮 😵 😎)
 *   • per-difficulty best times persisted in localStorage
 * ------------------------------------------------------------------
 */

'use strict';

/* ==================================================================
 * 1. Constants & difficulty presets
 * ================================================================== */

const PRESETS = {
  beginner: { rows: 9, cols: 9, mines: 10 },
  intermediate: { rows: 16, cols: 16, mines: 40 },
  expert: { rows: 16, cols: 30, mines: 99 }
};

const DIFFICULTY_PRESETS = PRESETS;
const DIFFICULTY_ORDER = ['beginner', 'intermediate', 'expert'];
const DIFFICULTY_LABELS = {
  beginner: '初级',
  intermediate: '中级',
  expert: '高级'
};

/** The eight neighbour offsets (up, down, left, right + 4 diagonals). */
const NEIGHBOR_OFFSETS = [
  [-1, -1], [-1, 0], [-1, 1],
  [0, -1], [0, 1],
  [1, -1], [1, 0], [1, 1]
];

/** Classic face states for the reset button. */
const SMILEY = {
  smile: '🙂',
  pressed: '😮',
  lose: '😵',
  win: '😎'
};
const SMILEY_STATES = SMILEY;

/** Cell mark states. */
const MARK_NONE = 'none';
const MARK_FLAG = 'flag';
const MARK_QUESTION = 'question';

const MAX_TIME = 999;
const MIN_TIME = 0;
const BASE_CELL_SIZE = 24;

/* --- localStorage keys (one best time per difficulty) --- */
const STORAGE_PREFIX = 'minesweeper_best_';
const STORAGE_KEYS = {
  beginner: STORAGE_PREFIX + 'beginner',
  intermediate: STORAGE_PREFIX + 'intermediate',
  expert: STORAGE_PREFIX + 'expert'
};
/** Aggregate snapshot, keeps every record in a single JSON document. */
const STORAGE_AGGREGATE_KEY = 'minesweeper_best_times';
/** Older / alternative key spellings we still read for migration. */
const STORAGE_LEGACY_KEYS = {
  beginner: ['minesweeper_bestTime_beginner', 'bestTime_beginner', 'best_beginner', 'ms_best_beginner'],
  intermediate: ['minesweeper_bestTime_intermediate', 'bestTime_intermediate', 'best_intermediate', 'ms_best_intermediate'],
  expert: ['minesweeper_bestTime_expert', 'bestTime_expert', 'best_expert', 'ms_best_expert']
};

/* ==================================================================
 * 2. Game state
 * ================================================================== */

const game = {
  /* --- configuration --- */
  difficulty: 'beginner',
  rows: PRESETS.beginner.rows,
  cols: PRESETS.beginner.cols,
  mines: PRESETS.beginner.mines,

  /* --- board --- */
  board: [],           // 2D array [row][col] of cell objects
  cells: [],           // flat list of every cell (handy for iteration)

  /* --- lifecycle --- */
  initialized: false,
  minesPlaced: false,  // mines are generated lazily, on the first reveal
  firstClick: true,    // true until the player opens their first cell
  started: false,      // the timer has been started at least once
  gameOver: false,     // frozen board (win or loss)
  won: false,
  status: 'ready',     // 'ready' | 'playing' | 'won' | 'lost'

  /* --- counters --- */
  flagsPlaced: 0,
  revealedCount: 0,

  /* --- clock --- */
  time: 0,             // elapsed seconds, 0..999
  elapsed: 0,          // alias of `time` (kept in sync)
  timerId: null,

  /* --- results --- */
  explodedCell: null,
  lastResult: null,    // 'win' | 'lose' | null
  lastRecord: false,   // did the last win set a new best time?
  bestTime: null,
  smileyFace: SMILEY.smile,

  /* --- cached DOM nodes --- */
  elements: {
    minefield: null,
    mineCounter: null,
    timer: null,
    smiley: null,
    bestTime: null,
    boardContainer: null,
    status: null,
    difficultyButtons: []
  }
};

/* ==================================================================
 * 3. Small utilities
 * ================================================================== */

const hasDOM = typeof document !== 'undefined' && typeof window !== 'undefined';

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

/** 3-digit LCD formatting, e.g. 10 -> "010", -3 -> "-03". */
function formatLCD(value) {
  const n = Math.trunc(Number(value)) || 0;
  if (n < 0) {
    return '-' + String(Math.min(99, Math.abs(n))).padStart(2, '0');
  }
  return String(clamp(n, 0, MAX_TIME)).padStart(3, '0');
}

function formatTime(seconds) {
  return formatLCD(seconds);
}

function markOf(cell) {
  if (!cell) return MARK_NONE;
  if (cell.isFlagged) return MARK_FLAG;
  if (cell.isQuestion) return MARK_QUESTION;
  return MARK_NONE;
}

/* ==================================================================
 * 4. localStorage helpers (best times)
 * ================================================================== */

function getStorage() {
  try {
    if (typeof localStorage !== 'undefined' && localStorage) return localStorage;
  } catch (err) {
    /* access can throw in private mode / sandboxed frames */
  }
  return null;
}

function storageGet(key) {
  const store = getStorage();
  if (!store) return null;
  try {
    return store.getItem(key);
  } catch (err) {
    return null;
  }
}

function storageSet(key, value) {
  const store = getStorage();
  if (!store) return false;
  try {
    store.setItem(key, String(value));
    return true;
  } catch (err) {
    return false;
  }
}

function readAggregate() {
  const raw = storageGet(STORAGE_AGGREGATE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (err) {
    return {};
  }
}

/** Best time in seconds for a difficulty, or null when never won. */
function getBestTime(difficulty) {
  const name = PRESETS[difficulty] ? difficulty : game.difficulty;
  const candidates = [STORAGE_KEYS[name]].concat(STORAGE_LEGACY_KEYS[name] || []);
  for (let i = 0; i < candidates.length; i++) {
    const raw = storageGet(candidates[i]);
    if (raw === null || raw === undefined || raw === '') continue;
    const parsed = parseInt(raw, 10);
    if (Number.isFinite(parsed) && parsed >= 0) return parsed;
  }
  const aggregate = readAggregate();
  const value = parseInt(aggregate[name], 10);
  if (Number.isFinite(value) && value >= 0) return value;
  return null;
}

/**
 * Persist a new best time when it beats the stored record.
 * @returns {{time:number|null, isRecord:boolean}}
 */
function saveBestTime(difficulty, seconds) {
  const name = PRESETS[difficulty] ? difficulty : game.difficulty;
  const value = Math.round(Number(seconds));
  const current = getBestTime(name);

  if (!Number.isFinite(value) || value < 0) {
    return { time: current, isRecord: false };
  }

  const clamped = clamp(value, MIN_TIME, MAX_TIME);
  const isRecord = current === null || clamped < current;

  if (isRecord) {
    storageSet(STORAGE_KEYS[name], clamped);
    const aggregate = readAggregate();
    aggregate[name] = clamped;
    storageSet(STORAGE_AGGREGATE_KEY, JSON.stringify(aggregate));
  }

  return { time: isRecord ? clamped : current, isRecord: isRecord };
}

const setBestTime = saveBestTime;

/* ==================================================================
 * 5. Board model
 * ================================================================== */

function createCell(row, col) {
  return {
    row: row,
    col: col,
    isMine: false,
    isRevealed: false,
    isFlagged: false,
    isQuestion: false,
    isExploded: false,
    isWrongFlag: false,
    adjacentMines: 0,
    el: null
  };
}

function createBoard(rows, cols) {
  const board = [];
  const cells = [];
  for (let r = 0; r < rows; r++) {
    const line = [];
    for (let c = 0; c < cols; c++) {
      const cell = createCell(r, c);
      line.push(cell);
      cells.push(cell);
    }
    board.push(line);
  }
  game.board = board;
  game.cells = cells;
  return board;
}

function isInside(row, col) {
  return row >= 0 && row < game.rows && col >= 0 && col < game.cols;
}

function getCell(row, col) {
  if (!isInside(row, col)) return null;
  const line = game.board[row];
  return (line && line[col]) || null;
}

/** Visit all 8 in-bounds neighbours of (row, col). */
function eachNeighbor(row, col, fn) {
  for (let i = 0; i < NEIGHBOR_OFFSETS.length; i++) {
    const nr = row + NEIGHBOR_OFFSETS[i][0];
    const nc = col + NEIGHBOR_OFFSETS[i][1];
    if (!isInside(nr, nc)) continue;
    fn(game.board[nr][nc], nr, nc);
  }
}

function getNeighbors(row, col) {
  const list = [];
  eachNeighbor(row, col, function (cell) { list.push(cell); });
  return list;
}

/** Number of mines in the 8 surrounding cells (0..8). */
function countAdjacentMines(row, col) {
  let count = 0;
  eachNeighbor(row, col, function (cell) {
    if (cell.isMine) count += 1;
  });
  return count;
}

function computeAdjacentCounts() {
  for (let r = 0; r < game.rows; r++) {
    for (let c = 0; c < game.cols; c++) {
      game.board[r][c].adjacentMines = countAdjacentMines(r, c);
    }
  }
}

/**
 * Lazily place the mines, keeping the very first clicked cell (and, when
 * there is room, its 8 neighbours) completely free of mines. This is the
 * "first click safety guarantee": the opening click can never explode.
 */
function placeMines(safeRow, safeCol) {
  const total = game.rows * game.cols;
  const mineCount = Math.min(game.mines, Math.max(0, total - 1));

  game.cells.forEach(function (cell) { cell.isMine = false; });

  const protectedIdx = new Set();
  const hasSafeCell = Number.isInteger(safeRow) && Number.isInteger(safeCol) &&
    safeRow >= 0 && safeRow < game.rows && safeCol >= 0 && safeCol < game.cols;

  if (hasSafeCell) {
    protectedIdx.add(safeRow * game.cols + safeCol);
    // Guarantee a wide opening (all neighbours empty too) when density allows.
    if (total - 9 >= mineCount) {
      for (let i = 0; i < NEIGHBOR_OFFSETS.length; i++) {
        const nr = safeRow + NEIGHBOR_OFFSETS[i][0];
        const nc = safeCol + NEIGHBOR_OFFSETS[i][1];
        if (isInside(nr, nc)) protectedIdx.add(nr * game.cols + nc);
      }
    }
  }

  let pool = [];
  for (let i = 0; i < total; i++) {
    if (!protectedIdx.has(i)) pool.push(i);
  }

  // Ultra-dense boards: relax the neighbourhood protection, keep the click safe.
  if (pool.length < mineCount && hasSafeCell) {
    const only = safeRow * game.cols + safeCol;
    pool = [];
    for (let i = 0; i < total; i++) {
      if (i !== only) pool.push(i);
    }
  }

  // Partial Fisher–Yates shuffle: pick `mineCount` distinct cells at random.
  const picks = Math.min(mineCount, pool.length);
  for (let i = 0; i < picks; i++) {
    const j = i + Math.floor(Math.random() * (pool.length - i));
    const tmp = pool[i];
    pool[i] = pool[j];
    pool[j] = tmp;
  }
  for (let i = 0; i < picks; i++) {
    const idx = pool[i];
    game.board[Math.floor(idx / game.cols)][idx % game.cols].isMine = true;
  }

  computeAdjacentCounts();
  game.minesPlaced = true;
  return game.board;
}

const generateMines = placeMines;

/* ==================================================================
 * 6. Rendering
 * ================================================================== */

function updateCellElement(cell) {
  const el = cell && cell.el;
  if (!el) return;

  const classes = ['cell'];
  let text = '';

  if (cell.isRevealed) {
    classes.push('revealed');
    if (cell.isMine) {
      classes.push('mine');
      text = '💣';
      if (cell.isExploded) classes.push('mine-exploded');
    } else if (cell.adjacentMines > 0) {
      classes.push('c-' + cell.adjacentMines);
      text = String(cell.adjacentMines);
    }
  } else if (cell.isFlagged) {
    classes.push('flagged');
    if (cell.isWrongFlag) {
      classes.push('wrong-flag');
      text = '❌';
    } else {
      text = '🚩';
    }
  } else if (cell.isQuestion) {
    classes.push('question');
    text = '❓';
  }

  el.className = classes.join(' ');
  el.textContent = text;

  const label = 'row ' + (cell.row + 1) + ', column ' + (cell.col + 1) + ', ' +
    (cell.isRevealed
      ? (cell.isMine ? 'mine' : (cell.adjacentMines || 'empty'))
      : (cell.isFlagged ? 'flagged' : (cell.isQuestion ? 'question' : 'hidden')));
  el.setAttribute('aria-label', label);
}

function renderBoard() {
  const el = game.elements.minefield;
  if (!el || typeof document === 'undefined') return;

  el.innerHTML = '';
  applyLayout();

  const fragment = document.createDocumentFragment();
  for (let r = 0; r < game.rows; r++) {
    for (let c = 0; c < game.cols; c++) {
      const cell = game.board[r][c];
      const div = document.createElement('div');
      div.className = 'cell';
      div.setAttribute('data-row', String(r));
      div.setAttribute('data-col', String(c));
      div.dataset.row = String(r);
      div.dataset.col = String(c);
      div.setAttribute('role', 'gridcell');
      cell.el = div;
      updateCellElement(cell);
      fragment.appendChild(div);
    }
  }
  el.appendChild(fragment);
}

function computeCellSize() {
  if (!hasDOM) return BASE_CELL_SIZE;
  const viewport = window.innerWidth || 1024;
  const available = Math.max(200, viewport - 96);
  const needed = game.cols * BASE_CELL_SIZE;
  if (needed <= available) return BASE_CELL_SIZE;
  return Math.max(13, Math.floor(available / game.cols));
}

function applyLayout() {
  const el = game.elements.minefield;
  if (!el) return;
  const size = computeCellSize();
  el.style.setProperty('--cols', String(game.cols));
  el.style.setProperty('--rows', String(game.rows));
  el.style.setProperty('--cell-size', size + 'px');
  el.style.gridTemplateColumns = 'repeat(' + game.cols + ', ' + size + 'px)';
}

/* ==================================================================
 * 7. HUD updates
 * ================================================================== */

function updateMineCounter() {
  const el = game.elements.mineCounter;
  if (!el) return;
  el.textContent = formatLCD(game.mines - game.flagsPlaced);
}

function updateTimerDisplay() {
  const el = game.elements.timer;
  if (!el) return;
  el.textContent = formatLCD(game.time);
}

function updateBestTimeDisplay() {
  game.bestTime = getBestTime(game.difficulty);
  const el = game.elements.bestTime;
  if (!el) return;
  el.textContent = game.bestTime === null ? '--' : game.bestTime + 's';
  if (game.lastRecord) {
    el.classList.add('record');
  } else {
    el.classList.remove('record');
  }
}

function updateDifficultyButtons() {
  const buttons = game.elements.difficultyButtons;
  for (let i = 0; i < buttons.length; i++) {
    const btn = buttons[i];
    const isActive = btn.getAttribute('data-diff') === game.difficulty;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  }
}

function setSmiley(face) {
  game.smileyFace = face;
  const btn = game.elements.smiley;
  if (!btn) return;
  btn.textContent = face;
  if (face === SMILEY.lose) {
    btn.dataset.state = 'lose';
  } else if (face === SMILEY.win) {
    btn.dataset.state = 'win';
  } else if (face === SMILEY.pressed) {
    btn.dataset.state = 'pressed';
  } else {
    btn.dataset.state = 'smile';
  }
}

function setStatus(status, message) {
  game.status = status;
  const el = game.elements.minefield;
  if (el) el.dataset.state = status;
  const statusEl = game.elements.status;
  if (statusEl) statusEl.textContent = message || '';
}

/* ==================================================================
 * 8. Timer
 * ================================================================== */

function startTimer() {
  if (game.timerId !== null) return;
  if (game.gameOver) return;
  game.started = true;
  if (game.status === 'ready') setStatus('playing', '');
  game.timerId = setInterval(tickTimer, 1000);
  updateTimerDisplay();
}

function tickTimer() {
  if (game.gameOver) {
    stopTimer();
    return;
  }
  game.time = clamp(game.time + 1, MIN_TIME, MAX_TIME);
  game.elapsed = game.time;
  updateTimerDisplay();
  if (game.time >= MAX_TIME) stopTimer();
}

function stopTimer() {
  if (game.timerId !== null) {
    clearInterval(game.timerId);
    game.timerId = null;
  }
}

function resetTimer() {
  stopTimer();
  game.time = 0;
  game.elapsed = 0;
  updateTimerDisplay();
}

/* ==================================================================
 * 9. Core mechanics
 * ================================================================== */

function markRevealed(cell) {
  if (!cell || cell.isRevealed) return false;
  cell.isRevealed = true;
  cell.isQuestion = false;
  game.revealedCount += 1;
  updateCellElement(cell);
  return true;
}

/**
 * Zero-cascade flood fill (BFS). Reveals the start cell and, whenever a
 * revealed cell has zero adjacent mines, every non-mine neighbour as well.
 * @returns {number} how many cells were opened
 */
function floodFill(startRow, startCol) {
  const start = getCell(startRow, startCol);
  if (!start) return 0;
  if (start.isRevealed || start.isFlagged || start.isMine) return 0;

  let opened = 0;
  const queue = [[startRow, startCol]];
  const visited = new Set([startRow * game.cols + startCol]);
  let head = 0;

  while (head < queue.length) {
    const current = queue[head];
    head += 1;
    const cell = getCell(current[0], current[1]);
    if (!cell || cell.isRevealed || cell.isFlagged || cell.isMine) continue;

    if (markRevealed(cell)) opened += 1;

    if (cell.adjacentMines === 0) {
      eachNeighbor(cell.row, cell.col, function (nb) {
        if (nb.isRevealed || nb.isFlagged || nb.isMine) return;
        const key = nb.row * game.cols + nb.col;
        if (visited.has(key)) return;
        visited.add(key);
        queue.push([nb.row, nb.col]);
      });
    }
  }
  return opened;
}

const cascade = floodFill;

/**
 * Reveal a single cell (left click).
 * Flags protect their cell, the first click is always safe, and every
 * action is ignored once the game is over.
 */
function revealCell(row, col) {
  if (game.gameOver) return false;
  const cell = getCell(row, col);
  if (!cell) return false;
  if (cell.isRevealed) return false;
  if (cell.isFlagged) return false; // flag protection: cannot be opened by accident

  if (!game.minesPlaced) {
    placeMines(row, col);
  }
  if (game.firstClick) {
    game.firstClick = false;
    startTimer();
  }

  if (cell.isMine) {
    loseGame(cell);
    return false;
  }

  floodFill(row, col);
  checkWin();
  return true;
}

const reveal = revealCell;

/**
 * Right-click cycle: none -> flag -> question -> none.
 * @returns {string} the new mark state
 */
function toggleFlag(row, col) {
  if (game.gameOver) return MARK_NONE;
  const cell = getCell(row, col);
  if (!cell || cell.isRevealed) return MARK_NONE;

  if (cell.isFlagged) {
    cell.isFlagged = false;
    cell.isQuestion = true;
    game.flagsPlaced = Math.max(0, game.flagsPlaced - 1);
  } else if (cell.isQuestion) {
    cell.isQuestion = false;
  } else {
    cell.isFlagged = true;
    cell.isQuestion = false;
    game.flagsPlaced += 1;
  }

  updateCellElement(cell);
  updateMineCounter();
  return markOf(cell);
}

const toggleMark = toggleFlag;

/**
 * Chording: on an already revealed number whose neighbouring flag count
 * matches the digit, open every remaining (unflagged) neighbour.
 * A misplaced flag therefore detonates a mine — exactly like the original.
 */
function chord(row, col) {
  if (game.gameOver) return false;
  const cell = getCell(row, col);
  if (!cell || !cell.isRevealed || cell.isMine) return false;
  if (cell.adjacentMines <= 0) return false;

  let flagCount = 0;
  const targets = [];
  eachNeighbor(row, col, function (nb) {
    if (nb.isFlagged) {
      flagCount += 1;
    } else if (!nb.isRevealed) {
      targets.push(nb);
    }
  });

  if (flagCount !== cell.adjacentMines) return false;
  if (targets.length === 0) return false;

  for (let i = 0; i < targets.length; i++) {
    if (game.gameOver) break;
    const nb = targets[i];
    if (nb.isRevealed || nb.isFlagged) continue;
    revealCell(nb.row, nb.col);
  }

  if (!game.gameOver) checkWin();
  return true;
}

const chordAt = chord;
const chordReveal = chord;

/* ==================================================================
 * 10. Win / loss resolution
 * ================================================================== */

function checkWin() {
  if (game.gameOver) return false;
  const safeCells = game.rows * game.cols - game.mines;
  if (game.revealedCount >= safeCells) {
    winGame();
    return true;
  }
  return false;
}

function winGame() {
  if (game.gameOver) return;
  game.gameOver = true;
  game.won = true;
  game.lastResult = 'win';
  stopTimer();

  // Auto-flag every remaining mine.
  for (let i = 0; i < game.cells.length; i++) {
    const cell = game.cells[i];
    if (cell.isMine && !cell.isFlagged) {
      cell.isFlagged = true;
      cell.isQuestion = false;
      game.flagsPlaced += 1;
      updateCellElement(cell);
    }
  }
  game.flagsPlaced = game.mines;
  updateMineCounter();
  setSmiley(SMILEY.win);

  const result = saveBestTime(game.difficulty, game.time);
  game.lastRecord = !!result.isRecord;
  game.bestTime = result.time;
  updateBestTimeDisplay();

  setStatus('won', '🎉 胜利！用时 ' + game.time + ' 秒' + (game.lastRecord ? ' · 新纪录！' : ''));
}

function loseGame(explodedCell) {
  if (game.gameOver) return;
  game.gameOver = true;
  game.won = false;
  game.lastResult = 'lose';
  stopTimer();

  if (explodedCell) {
    explodedCell.isExploded = true;
    explodedCell.isRevealed = true;
    explodedCell.isQuestion = false;
    game.explodedCell = explodedCell;
  }

  revealAllMines();
  setSmiley(SMILEY.lose);
  setStatus('lost', '💥 踩雷了！点击笑脸重新开始');
}

const gameOver = loseGame;

/** Show every mine, and cross out every flag that was placed wrongly. */
function revealAllMines() {
  for (let i = 0; i < game.cells.length; i++) {
    const cell = game.cells[i];
    if (cell.isMine) {
      if (!cell.isFlagged) {
        cell.isRevealed = true;
      }
      updateCellElement(cell);
    } else if (cell.isFlagged) {
      cell.isWrongFlag = true;
      updateCellElement(cell);
    }
  }
}

/* ==================================================================
 * 11. New game / difficulty
 * ================================================================== */

function newGame(difficulty) {
  if (typeof difficulty === 'string' && PRESETS[difficulty]) {
    game.difficulty = difficulty;
  }
  const preset = PRESETS[game.difficulty] || PRESETS.beginner;

  stopTimer();

  game.rows = preset.rows;
  game.cols = preset.cols;
  game.mines = preset.mines;

  game.time = 0;
  game.elapsed = 0;
  game.minesPlaced = false;
  game.firstClick = true;
  game.started = false;
  game.gameOver = false;
  game.won = false;
  game.lastResult = null;
  game.lastRecord = false;
  game.flagsPlaced = 0;
  game.revealedCount = 0;
  game.explodedCell = null;

  createBoard(game.rows, game.cols);
  renderBoard();

  updateMineCounter();
  updateTimerDisplay();
  updateDifficultyButtons();
  updateBestTimeDisplay();
  setSmiley(SMILEY.smile);
  setStatus('ready', DIFFICULTY_LABELS[game.difficulty] + ' · 点击任意格子开始');

  return game;
}

const startNewGame = newGame;
const resetGame = newGame;
const initGame = newGame;

function setDifficulty(difficulty) {
  if (!PRESETS[difficulty]) return game;
  return newGame(difficulty);
}

/* ==================================================================
 * 12. DOM events
 * ================================================================== */

function cacheElements() {
  if (!hasDOM) return;
  game.elements.minefield = document.getElementById('minefield');
  game.elements.mineCounter = document.getElementById('mine-counter');
  game.elements.timer = document.getElementById('timer');
  game.elements.smiley = document.getElementById('smiley-btn');
  game.elements.bestTime = document.getElementById('best-time');
  game.elements.boardContainer = document.getElementById('board-container');
  game.elements.status = document.getElementById('game-status');
  game.elements.difficultyButtons = Array.prototype.slice.call(
    document.querySelectorAll('.diff-btn')
  );
}

/** Resolve the cell object behind a DOM event. */
function cellFromEvent(event) {
  if (!event) return null;
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return null;
  const el = target.closest('.cell');
  if (!el) return null;
  const row = parseInt(el.getAttribute('data-row'), 10);
  const col = parseInt(el.getAttribute('data-col'), 10);
  if (!Number.isInteger(row) || !Number.isInteger(col)) return null;
  return getCell(row, col);
}

let suppressNextClick = false;
let touchTimer = null;

function handleContextMenu(event) {
  // Kill the browser context menu so right-click can cycle flags.
  event.preventDefault();
  const cell = cellFromEvent(event);
  if (!cell) return;
  toggleFlag(cell.row, cell.col);
}

function handleMouseDown(event) {
  const cell = cellFromEvent(event);
  if (!cell) return;
  if (game.gameOver) return;

  if (event.button === 0 || event.buttons === 3) {
    setSmiley(SMILEY.pressed);
  }
  // Both buttons pressed at once == chord.
  if (event.buttons === 3) {
    chord(cell.row, cell.col);
  }
}

function handleMouseUp() {
  if (game.gameOver) return;
  setSmiley(SMILEY.smile);
}

function handleClick(event) {
  if (suppressNextClick) {
    suppressNextClick = false;
    return;
  }
  const cell = cellFromEvent(event);
  if (!cell) return;
  revealCell(cell.row, cell.col);
}

function handleDoubleClick(event) {
  const cell = cellFromEvent(event);
  if (!cell) return;
  chord(cell.row, cell.col);
}

function handleAuxClick(event) {
  // Middle click chords too.
  if (event.button !== 1) return;
  event.preventDefault();
  const cell = cellFromEvent(event);
  if (!cell) return;
  chord(cell.row, cell.col);
}

function handleSmileyClick(event) {
  if (event) event.preventDefault();
  newGame(game.difficulty);
}

function handleTouchStart(event) {
  const cell = cellFromEvent(event);
  if (!cell || game.gameOver) return;
  if (touchTimer !== null) clearTimeout(touchTimer);
  touchTimer = setTimeout(function () {
    touchTimer = null;
    suppressNextClick = true;
    toggleFlag(cell.row, cell.col);
  }, 450);
}

function handleTouchEnd() {
  if (touchTimer !== null) {
    clearTimeout(touchTimer);
    touchTimer = null;
  }
}

function handleKeyDown(event) {
  const key = (event.key || '').toLowerCase();
  if (key === 'r') {
    newGame(game.difficulty);
  } else if (key === '1') {
    setDifficulty('beginner');
  } else if (key === '2') {
    setDifficulty('intermediate');
  } else if (key === '3') {
    setDifficulty('expert');
  }
}

function bindEvents() {
  if (!hasDOM) return;
  const el = game.elements;

  if (el.minefield) {
    el.minefield.addEventListener('contextmenu', handleContextMenu);
    el.minefield.addEventListener('mousedown', handleMouseDown);
    el.minefield.addEventListener('mouseup', handleMouseUp);
    el.minefield.addEventListener('click', handleClick);
    el.minefield.addEventListener('dblclick', handleDoubleClick);
    el.minefield.addEventListener('auxclick', handleAuxClick);
    el.minefield.addEventListener('touchstart', handleTouchStart, { passive: true });
    el.minefield.addEventListener('touchend', handleTouchEnd);
  }

  // Keep the pressed face from getting stuck when the pointer leaves the board.
  document.addEventListener('mouseup', handleMouseUp);

  if (el.smiley) {
    el.smiley.addEventListener('click', handleSmileyClick);
    el.smiley.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  }

  for (let i = 0; i < el.difficultyButtons.length; i++) {
    (function (btn) {
      btn.addEventListener('click', function () {
        setDifficulty(btn.getAttribute('data-diff'));
      });
    })(el.difficultyButtons[i]);
  }

  document.addEventListener('keydown', handleKeyDown);
  window.addEventListener('resize', applyLayout);

  // Belt & braces: never show the native menu over the game board.
  document.addEventListener('contextmenu', function (event) {
    const target = event.target;
    if (target && typeof target.closest === 'function' && target.closest('.window')) {
      event.preventDefault();
    }
  });
}

/* ==================================================================
 * 13. Bootstrap
 * ================================================================== */

function init() {
  if (game.initialized) return game;
  game.initialized = true;

  cacheElements();
  bindEvents();

  if (hasDOM && game.elements.minefield) {
    game.elements.minefield.setAttribute('role', 'grid');
  }

  newGame(game.difficulty);
  return game;
}

/* ==================================================================
 * 14. Public API
 * ================================================================== */

/* Methods are attached to the state object as well, so `game.revealCell(...)`
 * and `Minesweeper.revealCell(...)` both work. */
game.newGame = newGame;
game.startNewGame = newGame;
game.resetGame = newGame;
game.setDifficulty = setDifficulty;
game.revealCell = revealCell;
game.reveal = revealCell;
game.floodFill = floodFill;
game.cascade = floodFill;
game.toggleFlag = toggleFlag;
game.toggleMark = toggleFlag;
game.chord = chord;
game.placeMines = placeMines;
game.generateMines = placeMines;
game.computeAdjacentCounts = computeAdjacentCounts;
game.countAdjacentMines = countAdjacentMines;
game.getNeighbors = getNeighbors;
game.eachNeighbor = eachNeighbor;
game.getCell = getCell;
game.checkWin = checkWin;
game.winGame = winGame;
game.loseGame = loseGame;
game.startTimer = startTimer;
game.stopTimer = stopTimer;
game.resetTimer = resetTimer;
game.getBestTime = getBestTime;
game.saveBestTime = saveBestTime;
game.updateMineCounter = updateMineCounter;
game.updateTimerDisplay = updateTimerDisplay;
game.updateBestTimeDisplay = updateBestTimeDisplay;
game.renderBoard = renderBoard;
game.setSmiley = setSmiley;
game.formatLCD = formatLCD;
game.init = init;

const Minesweeper = {
  PRESETS: PRESETS,
  DIFFICULTY_PRESETS: DIFFICULTY_PRESETS,
  DIFFICULTY_ORDER: DIFFICULTY_ORDER,
  SMILEY: SMILEY,
  SMILEY_STATES: SMILEY_STATES,
  STORAGE_KEYS: STORAGE_KEYS,
  MAX_TIME: MAX_TIME,
  game: game,
  get state() { return game; },
  get board() { return game.board; },
  init: init,
  newGame: newGame,
  startNewGame: newGame,
  resetGame: newGame,
  initGame: newGame,
  setDifficulty: setDifficulty,
  revealCell: revealCell,
  reveal: revealCell,
  floodFill: floodFill,
  cascade: floodFill,
  toggleFlag: toggleFlag,
  toggleMark: toggleFlag,
  chord: chord,
  chordAt: chord,
  placeMines: placeMines,
  generateMines: placeMines,
  computeAdjacentCounts: computeAdjacentCounts,
  countAdjacentMines: countAdjacentMines,
  getNeighbors: getNeighbors,
  eachNeighbor: eachNeighbor,
  getCell: getCell,
  createBoard: createBoard,
  checkWin: checkWin,
  winGame: winGame,
  loseGame: loseGame,
  gameOver: loseGame,
  startTimer: startTimer,
  stopTimer: stopTimer,
  resetTimer: resetTimer,
  getBestTime: getBestTime,
  saveBestTime: saveBestTime,
  setBestTime: setBestTime,
  formatLCD: formatLCD,
  formatTime: formatTime,
  markOf: markOf,
  renderBoard: renderBoard,
  updateCellElement: updateCellElement,
  updateMineCounter: updateMineCounter,
  updateTimerDisplay: updateTimerDisplay,
  updateBestTimeDisplay: updateBestTimeDisplay,
  setSmiley: setSmiley
};

if (typeof window !== 'undefined') {
  window.Minesweeper = Minesweeper;
  window.minesweeper = Minesweeper;
  window.game = game;
  window.gameState = game;
  window.PRESETS = PRESETS;
}

if (hasDOM) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}
