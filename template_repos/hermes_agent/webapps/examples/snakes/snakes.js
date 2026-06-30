// Single-player Snakes. Pure client-side: the game loop, input, and rendering
// all run in the browser; the best score is kept in localStorage. No backend,
// no build step — edit this file and reload to see your changes.

const canvas = document.getElementById("board");
const ctx = canvas.getContext("2d");
const scoreEl = document.getElementById("score");
const bestEl = document.getElementById("best");
const overlay = document.getElementById("overlay");
const overlayTitle = document.getElementById("overlay-title");
const overlayText = document.getElementById("overlay-text");

const COLS = 24;
const ROWS = 24;
const CELL = canvas.width / COLS; // canvas is square, so width === height
const BEST_KEY = "humr-snakes-best";

const START_TICK_MS = 130; // time between moves; shrinks as you eat
const MIN_TICK_MS = 70;
const SPEEDUP_MS = 3;

// Direction vectors, keyed by both arrow keys and WASD.
const DIRS = {
  ArrowUp: { x: 0, y: -1 }, w: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 }, s: { x: 0, y: 1 },
  ArrowLeft: { x: -1, y: 0 }, a: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 }, d: { x: 1, y: 0 },
};

let snake, dir, nextDir, food, score, tickMs, acc, lastTime;
let state = "idle"; // "idle" | "running" | "paused" | "over"

let best = Number(localStorage.getItem(BEST_KEY) || 0);
bestEl.textContent = best;

function reset() {
  snake = [{ x: 8, y: 12 }, { x: 7, y: 12 }, { x: 6, y: 12 }];
  dir = { x: 1, y: 0 };
  nextDir = dir;
  score = 0;
  tickMs = START_TICK_MS;
  acc = 0;
  scoreEl.textContent = score;
  placeFood();
}

function placeFood() {
  const free = [];
  for (let y = 0; y < ROWS; y++) {
    for (let x = 0; x < COLS; x++) {
      if (!snake.some((s) => s.x === x && s.y === y)) free.push({ x, y });
    }
  }
  if (free.length === 0) { win(); return; }
  food = free[Math.floor(Math.random() * free.length)];
}

function start() {
  reset();
  state = "running";
  overlay.classList.add("hidden");
  lastTime = performance.now();
  requestAnimationFrame(loop);
}

function pause() {
  state = "paused";
  showOverlay("Paused", "Press Space to resume");
}

function resume() {
  state = "running";
  overlay.classList.add("hidden");
  lastTime = performance.now();
  acc = 0;
  requestAnimationFrame(loop);
}

function gameOver() {
  state = "over";
  recordBest();
  showOverlay("Game over", `Score ${score} · Press Space to play again`);
}

function win() {
  state = "over";
  recordBest();
  showOverlay("You win!", `You filled the board · Press Space to play again`);
}

function recordBest() {
  if (score > best) {
    best = score;
    localStorage.setItem(BEST_KEY, String(best));
    bestEl.textContent = best;
  }
}

function showOverlay(title, text) {
  overlayTitle.textContent = title;
  overlayText.textContent = text;
  overlay.classList.remove("hidden");
}

// Advance the snake one cell. Sets state to "over" on a collision.
function tick() {
  dir = nextDir;
  const head = { x: snake[0].x + dir.x, y: snake[0].y + dir.y };

  if (head.x < 0 || head.x >= COLS || head.y < 0 || head.y >= ROWS) {
    return gameOver();
  }

  const eating = head.x === food.x && head.y === food.y;
  // When not eating, the tail vacates its cell this move, so the head may
  // legally enter it. When eating, the tail stays, so check the whole body.
  const body = eating ? snake : snake.slice(0, -1);
  if (body.some((s) => s.x === head.x && s.y === head.y)) {
    return gameOver();
  }

  snake.unshift(head);
  if (eating) {
    score += 1;
    scoreEl.textContent = score;
    if (tickMs > MIN_TICK_MS) tickMs -= SPEEDUP_MS;
    placeFood();
  } else {
    snake.pop();
  }
}

function loop(now) {
  if (state !== "running") return;
  acc += now - lastTime;
  lastTime = now;
  // Step as many ticks as the elapsed time allows, so the speed stays steady
  // even if a frame is late.
  while (acc >= tickMs) {
    acc -= tickMs;
    tick();
    if (state !== "running") break;
  }
  draw();
  if (state === "running") requestAnimationFrame(loop);
}

function draw() {
  ctx.fillStyle = getCss("--panel");
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  drawCircle(food.x, food.y, getCss("--food"));
  snake.forEach((s, i) => {
    drawRect(s.x, s.y, getCss(i === 0 ? "--snake-head" : "--snake"));
  });
}

function drawRect(x, y, color) {
  const pad = 1;
  ctx.fillStyle = color;
  roundRect(x * CELL + pad, y * CELL + pad, CELL - 2 * pad, CELL - 2 * pad, 4);
  ctx.fill();
}

function drawCircle(x, y, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x * CELL + CELL / 2, y * CELL + CELL / 2, CELL / 2 - 2, 0, Math.PI * 2);
  ctx.fill();
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function getCss(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// --- input -----------------------------------------------------------------

window.addEventListener("keydown", (e) => {
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

  if (key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (state === "running") pause();
    else if (state === "paused") resume();
    else start();
    return;
  }

  const nd = DIRS[key];
  if (!nd) return;
  e.preventDefault();
  if (state !== "running") return;
  // Ignore a reversal into the snake's own neck.
  if (nd.x === -dir.x && nd.y === -dir.y) return;
  nextDir = nd;
});

// Touch: swipe to steer, tap to start/resume.
let touchStart = null;
canvas.addEventListener("touchstart", (e) => {
  touchStart = e.touches[0];
}, { passive: true });

canvas.addEventListener("touchend", (e) => {
  if (!touchStart) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touchStart.clientX;
  const dy = t.clientY - touchStart.clientY;
  touchStart = null;

  if (Math.abs(dx) < 20 && Math.abs(dy) < 20) {
    if (state !== "running") (state === "paused" ? resume() : start());
    return;
  }
  if (state !== "running") return;

  const nd = Math.abs(dx) > Math.abs(dy)
    ? (dx > 0 ? DIRS.ArrowRight : DIRS.ArrowLeft)
    : (dy > 0 ? DIRS.ArrowDown : DIRS.ArrowUp);
  if (nd.x === -dir.x && nd.y === -dir.y) return;
  nextDir = nd;
}, { passive: true });

// --- boot ------------------------------------------------------------------

reset();
draw();
