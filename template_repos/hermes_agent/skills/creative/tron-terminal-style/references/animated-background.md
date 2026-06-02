# Animated Background (Vanta.js)

The animated background is half the look. The frosted-glass `.panel` style was tuned around having something moving behind it — without one, the page reads flat. Always include this layer unless there's a reason not to (CSP, perf, etc.).

[Vanta.js](https://www.vantajs.com/) is a small wrapper around three.js / p5.js that renders animated WebGL backgrounds. ~3 lines of JS to drop in. We've tested three effects with the Tron palette:

| Effect | Vibe | Uses |
|---|---|---|
| **NET** | Slow-drifting cyan wireframe mesh, cursor-reactive. *Calmest, default.* | three.js |
| **HALO** | Iridescent oil-slick swirl from a center point. Cosmic, more visual weight. | three.js |
| **TOPOLOGY** | Sketchy topographic contour lines drifting across the screen. Map-like, organic. | p5.js |

Other Vanta effects (BIRDS, CLOUDS, FOG, RINGS, DOTS, GLOBE, WAVES, CELLS, TRUNK) work too — same wiring, just swap the script URL and the `VANTA.NAME({...})` call.

## Wiring (same for all effects)

### 1. HTML — `<div id="bg-fx">` early in body, scripts before `</body>`

```html
<body>
  <div id="bg-fx"></div>
  <!-- ...page content... -->

  <!-- pick ONE of the script blocks below -->
</body>
```

### 2. CSS — already in templates/style.css, but for reference

```css
#bg-fx {
  position: fixed; inset: 0; z-index: -2;
  pointer-events: none;   /* Vanta reads window mouse events directly */
}

/* IMPORTANT: html/body must NOT have an opaque background, or it'll cover
   the canvas (which sits at z-index: -2, behind body). The stylesheet
   already handles this; just don't add `background: var(--bg)` back. */
```

The `.panel` rule already uses `rgba(0, 24, 32, 0.32)` + `backdrop-filter: blur(6px)`, which is the frosted-glass treatment that lets the animation show through panels without making text unreadable. Don't make panels opaque or the effect dies.

### 3. Effect scripts — pick one

#### NET (default — calmest)

```html
<script src="https://cdn.jsdelivr.net/npm/three@0.134.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.net.min.js"></script>
<script>
  if (window.VANTA && window.VANTA.NET) {
    VANTA.NET({
      el: "#bg-fx",
      mouseControls: true, touchControls: true, gyroControls: false,
      minHeight: 200, minWidth: 200, scale: 1, scaleMobile: 1,
      color: 0x5ff7ff,           // --cyan
      backgroundColor: 0x00080d, // deep black behind the mesh
      points: 9.00,
      maxDistance: 22.00,
      spacing: 17.00,
      showDots: false
    });
  }
</script>
```

**Knobs**: `points` (density 6–12), `maxDistance` (web sparseness), `spacing` (grid pitch), `showDots` (small spheres at nodes — leave false for pure-line Tron look).

#### HALO (more dramatic)

```html
<script src="https://cdn.jsdelivr.net/npm/three@0.134.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.halo.min.js"></script>
<script>
  if (window.VANTA && window.VANTA.HALO) {
    VANTA.HALO({
      el: "#bg-fx",
      mouseControls: true, touchControls: true, gyroControls: false,
      backgroundColor: 0x00080d,
      baseColor: 0x0a4a5a,        // teal swirl tone
      amplitudeFactor: 1.20,
      size: 1.20,
      xOffset: 0.00, yOffset: 0.00
    });
  }
</script>
```

**Knobs**: `baseColor` (swirl hue — try `0x004060` deeper teal, `0x553300` amber-warning), `amplitudeFactor` 0.5–2.5, `size` 0.5–2.0, `xOffset`/`yOffset` shift the halo center off-screen for asymmetric framing.

#### TOPOLOGY (sketchy contour lines)

```html
<script src="https://cdn.jsdelivr.net/npm/p5@1.9.0/lib/p5.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.topology.min.js"></script>
<script>
  if (window.VANTA && window.VANTA.TOPOLOGY) {
    VANTA.TOPOLOGY({
      el: "#bg-fx",
      mouseControls: true, touchControls: true, gyroControls: false,
      backgroundColor: 0x00080d,
      color: 0x5ff7ff
    });
  }
</script>
```

Note: TOPOLOGY uses **p5.js** instead of three.js. If you're swapping between effects, swap the engine script too.

## Picking the right effect

- **Default to NET.** It's the calmest. For a working tool the user spends time with, calm wins.
- **HALO** if the app is more decorative / landing-page-ish than tool-ish.
- **TOPOLOGY** for anything map / geo / route-related, where the sketchy contours feel thematic.
- For "warning console" amber palette variant, swap `color: 0x5ff7ff` for `0xffb454` in NET, or set HALO's `baseColor: 0x553300`.

## Pitfalls (tested ones)

1. **"I see no animation"**
   - First check: `curl http://localhost:PORT/ | grep "VANTA"` — confirm scripts are in the response.
   - Second check: open devtools console for "WebGL not supported" or CSP errors.
   - Most common cause: `html` or `body` has an opaque `background` rule covering the canvas. The canvas is at `z-index: -2`, so anything painting body or html will hide it.

2. **"Patched the CSS but browser shows no change"** — browser/proxy cache. While iterating, add this to your Flask app:
   ```python
   @app.after_request
   def _no_cache(resp):
       resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
       resp.headers["Pragma"] = "no-cache"
       resp.headers["Expires"] = "0"
       return resp
   ```
   Then hard-refresh (Cmd+Shift+R) or use incognito. Remove (or gate behind a debug flag) before shipping.

3. **"Mesh only visible at the edges"** — panels are still opaque. The frosted `.panel` background `rgba(0, 24, 32, 0.32)` + `backdrop-filter: blur(6px)` is what makes the animation visible *through* panels. If someone "fixed" the panel background to be solid, restore the translucent one.

4. **"Clicks land on the wrong spot inside the panel"** — something is `transform: scale()`-ing the panel or one of its parents. `transform: scale` breaks pointer-event coordinate math. Use `font-size` / padding tweaks to size things instead. (Same root cause as the timepicker hit-detection bug.)

5. **`three.js` version pin matters.** Vanta 0.5.24 is built against three r134; using `three@latest` breaks it silently (renderer initializes but nothing draws). Stay on `three@0.134.0`. If a future Vanta release updates this, check their docs.

6. **CSP blocks the CDN** — if the host enforces a strict Content-Security-Policy, jsdelivr scripts fail. Either relax CSP for `cdn.jsdelivr.net` or self-host the two `.min.js` files.

7. **Mobile / low-end perf** — Vanta runs WebGL at full viewport. Wrap init in `if (matchMedia('(min-width: 800px) and (pointer: fine)').matches)` to skip on small/touch devices if perf becomes an issue.

8. **`backdrop-filter` browser support** — Chrome / Safari / Firefox all support it now, but very old browsers will just see the `rgba(...)` fill without the blur. Acceptable degradation; the panels still read fine.

## Custom p5.js sketches (when Vanta isn't thematic enough)

Vanta's library is intentionally generic. When the app has a strong theme (a transit planner, a music player, a code analyzer), a hand-rolled p5.js sketch in the same `#bg-fx` slot reads more intentional and only costs ~150 lines. Same wiring rules apply: mount canvas into `#bg-fx`, no opaque html/body background, panels stay frosted-glass.

**Use the `p5js` skill** for sketch authoring — palette, `pixelDensity(1)`, `disableFriendlyErrors`, instance mode. The Tron-specific glue is below.

### Skeleton (instance mode, mounts into `#bg-fx`)

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.11.3/p5.min.js"></script>
<script>
  p5.disableFriendlyErrors = true;
  const sketch = (p) => {
    const CYAN  = [95, 247, 255];   // --cyan
    const AMBER = [255, 180, 84];   // --amber
    const BG    = [0, 8, 13];       // matches Vanta's backgroundColor

    p.setup = function() {
      const c = p.createCanvas(p.windowWidth, p.windowHeight);
      c.parent("bg-fx");            // mount into the same div Vanta uses
      c.style("display", "block");
      p.frameRate(30);              // 30 is plenty for ambient bg; saves CPU
      p.pixelDensity(1);            // critical — retina overdraw kills perf
    };
    p.windowResized = function() {
      p.resizeCanvas(p.windowWidth, p.windowHeight);
    };
    p.draw = function() {
      p.background(BG[0], BG[1], BG[2]);   // opaque clear, no smear from prev frame
      // ...your scene...
    };
  };
  new p5(sketch);
</script>
```

### Recipe: vanishing-point rails (transit / route apps)

Wireframe rails converging to a horizon vanishing point with sleepers (ties) flowing toward the camera. Adds twinkling signal lights above the horizon and an occasional wireframe train crossing it. Calm by default — `SPEED = 0.45`, `frameRate(30)`, ~60 sleepers — won't distract from foreground panels.

Key parameters tuned for Tron palette: cyan rails/sleepers with alpha ramping from horizon (`alpha 30`) to camera (`alpha 200`); amber pinpoint headlights; horizon glow as a stack of cyan ellipses (radius 93, step 3) — small and tight, **not** a full-screen halo. Full source lives in the Caltrain planner's `app.py` — search for `trainBgSketch`.

**Anchoring overlays to the horizon line**: when drawing things that should sit *on* the horizon (a train, a building), translate to `(x, horizonY)` and draw with negative Y values for the body. The "wheels on the horizon" look comes from anchoring at exactly `horizonY`, not `horizonY - height*0.04` (that floats above).

**Glow sizing**: a layered ellipse glow looks bigger than its radius suggests because of the alpha stack. Default `r = 280, step 6` reads as a full-width horizon halo; **`r = 93, step 3`** is the tight, contained version that doesn't compete with foreground panels. Keep the `step` proportional to `r` (ratio ≈ 1:30) so gradient density stays the same.

### When custom > Vanta

- The app has a domain (transit, audio, code, weather) where one specific visual metaphor reinforces the purpose.
- You want a deterministic / seeded animation (Vanta is always live-randomized).
- CSP forbids the Vanta + three.js bundle but a single small p5 file is fine.
- You want elements to react to app state (e.g. the train passes faster when results load) — Vanta is read-only.

### When Vanta > custom

- Generic dashboard / tool / utility — no obvious theme.
- You want it done in 5 minutes with no creative-direction overhead.
- The user just wants "something moving and pretty back there."

## Self-hosting (when CSP doesn't allow CDN)

```bash
mkdir -p static/vendor
curl -L -o static/vendor/three.min.js https://cdn.jsdelivr.net/npm/three@0.134.0/build/three.min.js
curl -L -o static/vendor/vanta.net.min.js https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.net.min.js
# (or vanta.halo.min.js, vanta.topology.min.js as needed; swap three for p5 for topology)
```

Then change script `src` to `/static/vendor/...`.
