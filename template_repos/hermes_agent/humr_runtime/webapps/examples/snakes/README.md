# Snakes

A single-player Snakes game. Pure HTML/CSS/JavaScript — no backend and no build
step. The game loop, keyboard/touch input, and rendering all run in the browser;
your best score is saved in the browser's `localStorage`.

## Play

Arrow keys or **WASD** to move, **Space** to start and pause. On a touch screen,
swipe to steer and tap to start.

## Run it yourself

It's just static files, so any static file server works. The agent serves it with
Python's built-in server, bound to the loopback port the platform assigns:

```bash
python3 -m http.server "$WEBAPP_PORT" --bind 127.0.0.1
```

## Make it yours

Everything lives in three files:

- `index.html` — page structure (canvas, score, overlay).
- `style.css` — colors and layout. The palette is a handful of CSS variables at
  the top; change `--snake`, `--food`, or `--bg` and reload.
- `snakes.js` — the game. Try tweaking `COLS`/`ROWS` for a bigger board, or
  `START_TICK_MS` to change the speed.
