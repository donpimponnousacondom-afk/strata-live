# Case study: a 10-page LCARS engineering console (tkinter, Windows)

A desktop console that monitors a local LLM server, styled as a starship engineering panel. The warp core is the
engine; the sidebar holds 10 departments. It was built by an agent that couldn't really see its references (300 px
thumbnails) or its own output, so it had to be polished with the look loop.

## What looked wrong, and why

| Symptom | Cause |
|---|---|
| Stair-stepped elbow sweep, pill ends, dots, warp-core ellipses, gauges, chart lines | Tk canvas ovals, arcs, polygons and slanted lines have no anti-aliasing |
| A "blurry" UI in shared screenshots | the screenshots were a 1920 px window shrunk to 1097 px; the app itself was fine (DPI-aware) |
| A small hitch every second | a 60–70 ms redraw, mostly `Font.measure` round trips in a truncation binary search |
| Window lagging behind the mouse when resizing | a full rebuild per `<Configure>` event |

## What changed (layout untouched)

1. **`aa_canvas.Painter`.** Every curve and diagonal became an anti-aliased image piece at the same coordinates:
   - pills: crisp body plus AA ends;
   - the elbow: three rectangles, an AA convex corner and an AA concave fillet;
   - gauges: 27 AA arc segments, cached;
   - chart lines: cached by their points.
2. **Weight matching after an independent audit:**
   - pill tips pulled in 0.5 px, so the gaps were restored;
   - outlines 2.4 px inside the box;
   - lines 2.25 px;
   - triangles eroded 0.4 px;
   - ticks with butt length.
3. **One glow** under the warp-core chamber, recoloured by GPU load, and red in alert.
4. **Memoized `measure` and `fit`.**
5. **Coalesced resize** (at most every 50 ms, plus a final rebuild).
6. **Animation from 20 to 30 fps.** It was already time-based, so speeds were unchanged.

## Numbers (1920×1009, the same mock data)

| | before | after |
|---|---|---|
| redraw per second, average of pages | 30.2 ms (worst page 62) | 12.7 ms |
| animation | 18.2 fps, 1 hitch per second | about 30 fps, no late frames in steady state |
| 61-step drag-resize | 4.9 s at 11.7 fps | 1.46 s at 21 fps |
| new dependencies | — | none (stdlib only, Tk 8.6 PNG) |

## Lessons for agents

- **Look at your output zoomed in, every time.** Most defects are invisible at 100% and obvious at 3×.
- **Separate "how it's drawn" from "where it is".** A quality pass that keeps coordinates is easy to review, and
  safe.
- **Profile before optimizing.** The slow part wasn't the shapes (anti-aliasing was cheap); it was text measuring,
  which nobody suspected.
- **Ask for an unanchored audit.** It found the half-pixel drifts we had stopped seeing.
- **Mockups from an image model** give a target for finish (glow, rounding, weight) in minutes. Never copy their
  text.
