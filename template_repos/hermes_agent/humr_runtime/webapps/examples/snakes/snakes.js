// Single-player Snakes. Pure client-side: the game loop, input, and rendering
// all run in the browser; the best score is kept in localStorage. No backend,
// no build step — edit this file and reload to see your changes.
//
// Rendering is decoupled from the game tick. A requestAnimationFrame loop runs
// continuously, so the food pulses, the background breathes, and particles
// animate even while idle; the snake itself only advances on a fixed time step.
// Between steps the body glides smoothly by interpolating its previous and
// current cells, so movement looks fluid even though the logic stays grid-based.
// Colors are read from the CSS variables in style.css — re-theme there.

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

let snake, prevSnake, dir, nextDir, food, foodSpawn;
let score, tickMs, acc, lastTime, animTime;
let state = "idle"; // "idle" | "running" | "paused" | "over"
let particles = [];
let shake = 0;
let flash = 0;

const theme = readTheme();
let best = Number(localStorage.getItem(BEST_KEY) || 0);
bestEl.textContent = best;

function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return {
    panel: v("--panel"),
    gridRgb: v("--grid-rgb"),
    accentRgb: v("--accent-rgb"),
    snakeHead: v("--snake-head"),
    snake: v("--snake"),
    snakeTail: v("--snake-tail"),
    food: v("--food"),
    foodCore: v("--food-core"),
    foodDeep: v("--food-deep"),
    foodRgb: v("--food-rgb"),
  };
}

function clone(arr) { return arr.map((c) => ({ x: c.x, y: c.y })); }
function lerp(a, b, t) { return a + (b - a) * t; }

function reset() {
  snake = [{ x: 8, y: 12 }, { x: 7, y: 12 }, { x: 6, y: 12 }];
  prevSnake = clone(snake);
  dir = { x: 1, y: 0 };
  nextDir = dir;
  score = 0;
  tickMs = START_TICK_MS;
  acc = 0;
  particles = [];
  shake = 0;
  flash = 0;
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
  foodSpawn = animTime;
}

function start() {
  reset();
  state = "running";
  acc = 0;
  prevSnake = clone(snake);
  overlay.classList.add("hidden");
}

function pause() {
  state = "paused";
  prevSnake = clone(snake);
  showOverlay("Paused", "Press Space to resume");
}

function resume() {
  state = "running";
  acc = 0;
  prevSnake = clone(snake);
  overlay.classList.add("hidden");
}

function gameOver() {
  state = "over";
  shake = 7;
  flash = 0.5;
  burst(snake[0].x, snake[0].y, theme.food, 18);
  recordBest();
  showOverlay("Game over", `Score ${score} · Press Space to play again`);
}

function win() {
  state = "over";
  flash = 0.5;
  recordBest();
  showOverlay("You win!", "You filled the board · Press Space to play again");
}

function recordBest() {
  if (score > best) {
    best = score;
    localStorage.setItem(BEST_KEY, String(best));
    bestEl.textContent = best;
    pop(bestEl);
  }
}

function showOverlay(title, text) {
  overlayTitle.textContent = title;
  overlayText.textContent = text;
  overlay.classList.remove("hidden");
}

// Restart a CSS animation on an element (used for the score "pop").
function pop(el) {
  el.classList.remove("pop");
  void el.offsetWidth;
  el.classList.add("pop");
}

// Advance the snake one cell. Sets state to "over" on a collision.
function tick() {
  prevSnake = clone(snake);
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
    pop(scoreEl);
    burst(food.x, food.y, theme.snake, 14);
    if (tickMs > MIN_TICK_MS) tickMs -= SPEEDUP_MS;
    placeFood();
  } else {
    snake.pop();
  }
}

// --- particles (eat sparks / crash burst) ----------------------------------

function burst(cx, cy, color, n) {
  const x = cx * CELL + CELL / 2;
  const y = cy * CELL + CELL / 2;
  for (let i = 0; i < n; i++) {
    const a = (Math.PI * 2 * i) / n + Math.random() * 0.5;
    const sp = 40 + Math.random() * 100;
    particles.push({
      x, y,
      vx: Math.cos(a) * sp,
      vy: Math.sin(a) * sp,
      life: 1,
      decay: 1.6 + Math.random() * 1.3,
      size: 1.5 + Math.random() * 2.5,
      color,
    });
  }
  if (particles.length > 220) particles.splice(0, particles.length - 220);
}

function updateParticles(dt) {
  const s = dt / 1000;
  for (const p of particles) {
    p.x += p.vx * s;
    p.y += p.vy * s;
    p.vx *= 0.9;
    p.vy *= 0.9;
    p.life -= p.decay * s;
  }
  particles = particles.filter((p) => p.life > 0);
}

// --- main loop: always rendering, ticking only while running ---------------

function frame(now) {
  const dt = Math.min(now - lastTime, 100); // clamp tab-refocus jumps
  lastTime = now;
  animTime += dt;

  if (state === "running") {
    acc += dt;
    while (acc >= tickMs) {
      acc -= tickMs;
      tick();
      if (state !== "running") break;
    }
  }

  shake = shake > 0.1 ? shake * Math.pow(0.0015, dt / 1000) : 0;
  flash = flash > 0.002 ? flash * Math.pow(0.02, dt / 1000) : 0;
  updateParticles(dt);
  draw();
  requestAnimationFrame(frame);
}

// --- rendering -------------------------------------------------------------

function draw() {
  ctx.save();
  if (shake > 0.1) {
    ctx.translate((Math.random() - 0.5) * shake, (Math.random() - 0.5) * shake);
  }
  drawBackground();
  drawFood();
  drawSnake();
  drawParticles();
  ctx.restore();

  if (flash > 0.002) {
    ctx.fillStyle = `rgba(${theme.foodRgb}, ${flash * 0.5})`;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
}

function drawBackground() {
  ctx.fillStyle = theme.panel;
  ctx.fillRect(-12, -12, canvas.width + 24, canvas.height + 24);

  // A soft highlight that slowly drifts across the board.
  const gx = canvas.width * (0.5 + 0.32 * Math.sin(animTime * 0.0004));
  const gy = canvas.height * (0.42 + 0.3 * Math.cos(animTime * 0.0005));
  const glow = ctx.createRadialGradient(gx, gy, 0, gx, gy, canvas.width * 0.7);
  glow.addColorStop(0, `rgba(${theme.accentRgb}, 0.06)`);
  glow.addColorStop(1, `rgba(${theme.accentRgb}, 0)`);
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Faint grid that breathes.
  const breath = 0.5 + 0.5 * Math.sin(animTime * 0.001);
  ctx.strokeStyle = `rgba(${theme.gridRgb}, ${0.03 + 0.025 * breath})`;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 1; i < COLS; i++) {
    ctx.moveTo(i * CELL, 0); ctx.lineTo(i * CELL, canvas.height);
    ctx.moveTo(0, i * CELL); ctx.lineTo(canvas.width, i * CELL);
  }
  ctx.stroke();

  // Vignette.
  const vig = ctx.createRadialGradient(
    canvas.width / 2, canvas.height / 2, canvas.width * 0.32,
    canvas.width / 2, canvas.height / 2, canvas.width * 0.72,
  );
  vig.addColorStop(0, "rgba(0,0,0,0)");
  vig.addColorStop(1, "rgba(0,0,0,0.4)");
  ctx.fillStyle = vig;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

// Interpolated pixel centers for each segment (the glide).
function renderPoints() {
  const t = state === "running" ? Math.min(acc / tickMs, 1) : 1;
  return snake.map((c, i) => {
    const p = prevSnake[i] || c;
    return {
      x: (lerp(p.x, c.x, t) + 0.5) * CELL,
      y: (lerp(p.y, c.y, t) + 0.5) * CELL,
    };
  });
}

function drawSnake() {
  const pts = renderPoints();
  const head = pts[0];
  const tail = pts[pts.length - 1];
  const dead = state === "over";

  // Glowing tube with a head -> tail gradient.
  const grad = ctx.createLinearGradient(head.x, head.y, tail.x, tail.y);
  grad.addColorStop(0, theme.snakeHead);
  grad.addColorStop(0.5, theme.snake);
  grad.addColorStop(1, theme.snakeTail);
  ctx.strokeStyle = grad;
  ctx.lineWidth = CELL * 0.8;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.shadowBlur = dead ? 4 : 14;
  ctx.shadowColor = `rgba(${theme.accentRgb}, ${dead ? 0.25 : 0.7})`;
  ctx.beginPath();
  ctx.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Head sheen + eyes (pointing in the travel direction).
  ctx.fillStyle = theme.snakeHead;
  ctx.beginPath();
  ctx.arc(head.x, head.y, CELL * 0.42, 0, Math.PI * 2);
  ctx.fill();

  const perp = { x: -dir.y, y: dir.x };
  for (const side of [1, -1]) {
    const ex = head.x + dir.x * CELL * 0.12 + perp.x * side * CELL * 0.2;
    const ey = head.y + dir.y * CELL * 0.12 + perp.y * side * CELL * 0.2;
    ctx.fillStyle = "#0b0d12";
    ctx.beginPath();
    ctx.arc(ex, ey, CELL * 0.11, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#eafff0";
    ctx.beginPath();
    ctx.arc(ex + dir.x * 1.3, ey + dir.y * 1.3, CELL * 0.045, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawFood() {
  const fx = (food.x + 0.5) * CELL;
  const fy = (food.y + 0.5) * CELL;
  const pulse = 0.5 + 0.5 * Math.sin(animTime * 0.006);
  const spawn = Math.min(1, (animTime - foodSpawn) / 220);
  const r = CELL * 0.3 * (0.55 + 0.45 * spawn) * (1 + 0.12 * pulse);

  // Halo ring.
  ctx.strokeStyle = `rgba(${theme.foodRgb}, ${0.32 * pulse * spawn})`;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(fx, fy, r * (1.8 + 0.4 * pulse), 0, Math.PI * 2);
  ctx.stroke();

  // Glowing orb.
  ctx.shadowBlur = 10 + 12 * pulse;
  ctx.shadowColor = theme.food;
  const g = ctx.createRadialGradient(fx - r * 0.3, fy - r * 0.3, r * 0.1, fx, fy, r);
  g.addColorStop(0, theme.foodCore);
  g.addColorStop(0.55, theme.food);
  g.addColorStop(1, theme.foodDeep);
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(fx, fy, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;
}

function drawParticles() {
  for (const p of particles) {
    ctx.globalAlpha = Math.max(0, p.life);
    ctx.fillStyle = p.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.size * Math.max(0.2, p.life), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
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

animTime = 0;
lastTime = performance.now();
reset();
requestAnimationFrame(frame);
