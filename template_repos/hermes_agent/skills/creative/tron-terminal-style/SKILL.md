---
name: tron-terminal-style
description: Tron / 90s-terminal HTML/CSS theme — deep teal-black bg, cyan + amber accents, JetBrains Mono, frosted-glass HUD panels with corner ticks and "// label" tags, layered over an animated Vanta.js WebGL background. Drop-in stylesheet for any small Flask/HTML web tool.
---

# Tron Terminal Style

A dark, Tron-clean retro-terminal look. Use when the user wants a "futuristic", "HUD", "90s terminal", or "Tron-like" feel for an internal tool, dashboard, or single-page form. NOT synthwave (no pink, no grid floor, no sun). NOT cyberpunk magenta. Vibe: cold cyan, warm amber accent, scanlines, sharp corners, monospace, animated WebGL background drifting behind everything.

## When to use

- Small internal tools, dashboards, control panels, planners, monitors
- User explicitly asks for "Tron", "terminal", "HUD", "retro-futuristic", "90s sci-fi"
- User says "I want it to look futuristic but clean"

Do NOT use this style if the user wants:
- A real product UI (use a system like Tailwind/shadcn)
- Light mode
- Anything pastel, soft, or warm-overall
- A page with very dense data tables (the animated background can fight with row borders — kill scanlines and use NET-only at low density)

## The two pillars

This style is built on **two elements that have to work together**:

1. **Frosted-glass `.panel`** (`rgba(0, 24, 32, 0.32)` + `backdrop-filter: blur(6px)`)
   — sharp 0-radius cyan-bordered HUD frames with corner ticks and a `// LABEL` tag. Translucent so the animated background shows through, blurred so text stays readable.

2. **Animated WebGL background** via [Vanta.js](https://www.vantajs.com/) (NET / HALO / TOPOLOGY)
   — drifts slowly behind everything. The frosted panels were tuned around having one of these. Without it the page looks flat.

Either alone is half the look. Wire both. See `references/animated-background.md` for the recipe.

## Design tokens

```css
:root {
  --bg:        #001218;
  --bg-2:      #001a23;
  --line:      #0e3a4a;
  --line-2:    #15596f;
  --text:      #d4f5ff;
  --muted:     #5b8a99;
  --cyan:      #5ff7ff;     /* primary accent — borders, h1, buttons */
  --cyan-dim:  #2bb8c7;     /* secondary accent — labels, table headers */
  --amber:     #ffb454;     /* highlight — selected/target value */
  --green:     #6fffb0;     /* positive (early, success) */
  --red:       #ff7a7a;     /* negative (late, error) */
}
```

**Color rules**:
- Cyan = chrome (frames, headings, primary action)
- Amber = the *one* thing the user is looking at (target value, selected item, highlighted row)
- Green/red ONLY for delta/state semantics, never decorative
- No pink, no purple, no magenta, no bright yellow buttons

## Typography

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
```

Body uses JetBrains Mono with `font-size: 14px; line-height: 1.4`. Headings/labels are uppercase with wide letter-spacing (3-5px). Body text stays normal case.

## Page skeleton

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>YOUR APP</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div id="bg-fx"></div>          <!-- Vanta canvas mounts here -->

  <div class="layout">
    <h1>App Name Here</h1>

    <aside class="panel">
      <span class="panel-label">// control</span>
      <h2>Section Title</h2>
      <form>
        <div class="field">
          <label for="x">Field Label</label>
          <input id="x" name="x">
        </div>
        <button type="submit">Submit</button>
      </form>
    </aside>

    <section class="panel">
      <span class="panel-label">// output</span>
      <h2>Results</h2>
      <!-- table or empty state here -->
    </section>

    <footer class="panel footer-panel">
      <span class="panel-label">// status</span>
      <small>System metadata, last-updated stamp, etc.</small>
    </footer>
  </div>

  <!-- Vanta scripts last — see references/animated-background.md -->
</body>
</html>
```

`.layout` is a 2-column grid that collapses below 880px. For single-column apps, set the layout to `grid-template-columns: 1fr; max-width: 640px;`.

The body uses `display: flex; flex-direction: column; justify-content: center; min-height: 100vh` so short pages center vertically in the viewport while taller pages flow naturally.

**Every anchored UI element belongs in a `.panel`.** Bare text floating directly on the background reads as "forgotten/unstyled", not as design — even a one-line footer should be `<footer class="panel footer-panel">` with a `// STATUS` label.

## Stylesheet

`templates/style.css` is the complete drop-in stylesheet. It provides:

- Vertically-centered flex body with cyan radial vignette + scanline overlay (no opaque base — Vanta's canvas paints behind body)
- `.layout` — 2-column grid, h1 spans both columns, collapses under 880px
- `.panel` — frosted-glass HUD frame: translucent fill + `backdrop-filter: blur(6px)` + cyan border + corner ticks + glow shadow
- `.panel-label` — uppercase `// LABEL` tag floating above panel top-left
- `.footer-panel` — full-width thin status-bar variant
- Form inputs/selects with cyan focus glow, custom CSS-only select arrow
- `button` — outlined cyan, fills on hover with text inverted to dark
- `table` — tabular-nums, cyan-dim uppercase headers, hover row gets cyan inset bar
- `h1` — wide letter-spacing, cyan glow text-shadow, decorative `▮` brackets
- `.target` (amber glow), `.early` (green), `.late` (red), `.empty` (centered placeholder), `.cursor` (blinking `_` for headings)

## Pitfalls

1. **Never set an opaque `background` on `html` or `body`.** The Vanta canvas sits at `z-index: -2`; an opaque body covers it. The stylesheet leaves body bg unset for this reason. If you skip Vanta, set `background: #00080d` on body explicitly.
2. **Don't use border-radius.** Sharp 0-radius corners are the whole point. Even `border-radius: 4px` ruins it.
3. **Don't make panels opaque.** The frosted-glass blur is what lets the animated background show through readably. If text is hard to read, increase the panel tint alpha (e.g. `0.45`) or the blur (e.g. `10px`), don't kill the translucency.
4. **Buttons stay outlined**, never filled by default. Filled cyan only on `:hover`.
5. **Scanlines are subtle** — `body::after` opacity is intentionally low. Don't crank it up; readable text matters.
6. **One amber thing per view.** Amber is the eye-magnet; if you highlight three things in amber the magic dies.
7. **`scrollbar-gutter: stable` on `<html>`** prevents layout jump when results appear.
8. **Don't `transform: scale()` panels or anything inside them.** `transform: scale` breaks pointer-event coordinates — clicks land on the wrong spot. Resize via padding / font-size instead.
9. **No bare text on the page.** Wrap in `.panel` with a `// LABEL`.

## Variants

- **Amber-primary**: swap `--cyan` ↔ `--amber` semantic roles for an "alert / warning console" feel. Also swap the Vanta `color`/`baseColor` to amber. Use sparingly — cyan-primary reads calmer.
- **Single-column**: set `.layout { grid-template-columns: 1fr; max-width: 640px; }`.
- **No scanlines**: delete the `body::after` block — useful on dense data tables where they fight with row borders.
- **No animation**: skip the Vanta scripts and add `body { background: #00080d; }`. Panels still look fine, just less alive.
- **Different Vanta effect**: see `references/animated-background.md` for HALO (cosmic swirl) and TOPOLOGY (sketchy contour lines).

## Iterating on the style (dev workflow)

When tweaking this style live, **always set no-cache headers** during development. Browsers and reverse proxies aggressively cache HTML/CSS, so patches will appear to do nothing even though the server is serving the new version.

Flask one-liner to drop in next to `app = Flask(__name__)`:

```python
@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp
```

Remove (or scope to a debug flag) before shipping. If the user reports "no difference" after a CSS patch, this is the most likely culprit — verify with `curl` before assuming the patch is wrong.

**Diagnosis order when "I see no change":**
1. `curl http://127.0.0.1:$PORT/ | grep <selector-you-just-edited>` — confirm server has the new CSS.
2. If yes, it's cache. Add no-cache headers, restart, hard-refresh (Cmd+Shift+R) or open in incognito.
3. Only then suspect a CSS issue (specificity, an unrelated `html`/`body` rule painting over it, etc.).

**Quick translucency test**: if a panel-related patch seems to have no effect, temporarily set `.panel { background: transparent !important; }` and refresh. If you now see the animation right through where the panel was, the previous patch was fine and this is the cache issue. If you still see solid color, something else is painting (check `html`/`body` rules).

## Files

- `templates/style.css` — drop-in stylesheet
- `templates/index.html` — minimal example page using the style + Vanta NET background
- `references/animated-background.md` — Vanta.js integration: NET / HALO / TOPOLOGY recipes, palette knobs, pitfalls
