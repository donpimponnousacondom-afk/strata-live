---
name: jujitsu-awesome-dashboards
description: Build or polish native Windows desktop dashboards, telemetry panels and sci-fi consoles (Star Trek LCARS and similar) in Python/tkinter so they look crisp and run smoothly. It covers seeing your own UI (window screenshots, zoomed crops, before/after sheets), anti-aliased canvas shapes (Tk draws curves jagged), fonts and DPI on Windows, frame pacing and resize jank, and mock data for screenshots. Use it when building a dashboard, telemetry, monitor or console UI, when one "looks rough/blurry/jagged", "stutters", "lags when resizing", or must "look like LCARS / a starship panel", or when you need to screenshot, compare or measure a Tk app on Windows.
---

# Jujitsu awesome dashboards

Make a desktop panel look like the picture in your head and move like a real instrument. Don't brute-force it: use
what the platform already does well (Tk's PNG decoder, GDI text, the canvas' own rectangles, Windows' PrintWindow)
and spend effort only where it shows.

Everything here is **Windows-native and standard library**. You need Python 3.9+ and its bundled tkinter (Tk 8.6),
and nothing to `pip install`. The scripts are in `scripts/`; the depth is in `references/`.

**Shell notes (Windows):**
- Run scripts with `py -3 scripts\x.py` (or the full path to python.exe). A bare `python` may open the Microsoft
  Store.
- Set environment variables with `set X=...` in cmd and `$env:X = "..."` in PowerShell.
- Continue long lines with `^` in cmd and a backtick in PowerShell.
- Quote `@file` arguments in PowerShell: `'@prompt.txt'`.

| Script | What it's for |
|---|---|
| `scripts/win_capture.py` | screenshot a window by title (works while covered), set its exact client size, crop, zoom |
| `scripts/imgtool.py` | zoom crops, before/after stacks, contact sheets, the same crop of every page (Tk only, no Pillow) |
| `scripts/tkprobe.py` | run any Tk app unchanged and get frame pacing, per-method timings, a drag-resize test, an item count, a profile |
| `scripts/aa_canvas.py` | `Painter`: anti-aliased ovals, rings, arcs, polygons, lines, pills, elbows, glows for a `tk.Canvas` |
| `scripts/starter_dashboard.py` | a small LCARS panel with the core patterns (AA shapes, layers, cached measuring, coalesced resize, time-based 30 fps animation, DPI, font fallbacks, seeded data, `--selftest`); copy from it |
| `scripts/image_edit.py` | remaster a screenshot into a mockup through any OpenAI-compatible images API (env vars for endpoint and key) |

## The loop: you must SEE what you build

Most ugly dashboards come from an agent that designed "by ear": it wrote drawing code, never looked at the result,
and judged from blurry thumbnails. Close the loop every time you change the UI:

1. **Give the app a selftest switch** that starts it with deterministic data, waits, and closes by itself (for
   example `--selftest 8 --page=3`). Also support a mock or replay data source, so every page has realistic content
   without the real backend. See `references/look-loop.md`.
2. **Capture at the sizes the user really runs.** Use their window size and a maximized 1080p window at least:
   `py -3 scripts\win_capture.py out\page03.png --title "My Panel (selftest)" --size 1280x780`.
   - Use a unique title (or `--exact`).
   - Use the same DPI awareness as the app (`--dpi-awareness 1` for most Tk apps).
   - Capture at 100% display scaling, and at the user's real scaling.
3. **Zoom in.** At 100% you can't judge edges. `py -3 scripts\imgtool.py zoom out\page03.png z.png --crop 0,0,500,300 --factor 3`.
   Look at the curves, pill ends, dots, chart lines and text edges.
4. **Compare before and after** with the same crop on every page: `imgtool.py pairs cmp before\1280x780 after\1280x780 --crop ... --factor 2`
   (one size folder per run).
5. **Get an unanchored second opinion.** Have a fresh agent audit the before/after shots, without your conclusions,
   for seams, jaggies, size drift and anything that got worse. It will catch half-pixel regressions you have stopped
   seeing.

Keep shots in a folder per state (`shots/before`, `shots/after`) with a page-per-file naming. They become the PR's
evidence.

## Rendering quality with Tk: the rules

Tk's canvas draws **ovals, arcs, polygons and slanted lines without anti-aliasing** on Windows. That one fact makes
most Tk dashboards look cheap. Rectangles and text are fine (text gets ClearType). So:

- **Keep axis-aligned rectangles as canvas rectangles.** They are already pixel-perfect and cheap.
- **Draw every curve or diagonal through `aa_canvas.Painter`.** Its methods mirror the `create_*` calls and return
  item ids: `oval`, `oval_ring`, `arc`, `polygon`, `line`, `segment`, `pill`, `corner` (LCARS elbows), `glow`. Under
  the hood it makes signed-distance coverage masks, then a PNG, then `PhotoImage`, all cached.
- **Split rounded shapes into a crisp body plus AA ends, meeting on whole pixels.** Example: a pill is one rectangle
  and two cap images. An elbow is three rectangles, a convex `corner` and a concave fillet (`inside=False`). Seams
  only appear when a piece edge falls on a fractional pixel.
- **Recolour, don't recreate.** Animation and alert flashing go through `aa.recolor(item, colour)`, which is a cache
  hit after the first time. Call `aa.prune()` after `canvas.delete(...)`.
- **Compensate for perceived weight.** An AA stroke reads lighter and an AA shape reads about half a pixel bigger
  than Tk's hard-edged ones:
  - lines: Tk's 2 px becomes 2.25 px;
  - outlines: 2 px becomes 2.4 px. `oval_ring` centres the stroke on the box, so pass the box inset by 1 px;
  - small triangles: `erode=0.4`;
  - pill tips: pulled in 0.5 px, so the gaps between neighbouring pills stay the same.
- **Keep layout fixed when polishing.** A quality pass changes how things are drawn, not where. Diff the layout:
  nothing should move more than 1 px.

Details, pitfalls and why each rule exists: `references/tk-rendering.md`.

## Type and DPI on Windows

- **Call `ctypes.windll.shcore.SetProcessDpiAwareness(1)` before `tk.Tk()`.** Otherwise Windows bitmap-stretches the
  whole window on scaled displays, and everything is blurry.
- **Pick fonts from a fallback list of installed families.** `tkfont.families()` lists them. For LCARS:
  - **Bahnschrift SemiBold Condensed** ships with Windows 10 and 11;
  - **Antonio** (OFL) is the classic LCARS look if you can bundle or install it;
  - **Segoe UI** for body text.
- Uppercase labels, condensed type, and numbers right-aligned in columns.
- **Text measuring is the hidden cost.** Every `Font.measure()` is a round trip into Tk. A `fit()` that truncates
  with a binary search does about ten of them per label, and redraws repeat them every second. Memoize
  `measure(text, font)` and `fit(text, font, room)` (see `starter_dashboard.py`). On a real console this one change
  cut the per-second redraw from 62 ms to 7 ms.

## Smoothness: budgets and fixes

Measure first: `py -3 scripts\tkprobe.py --frame "mymod:Window.animate" --methods "mymod:Window.redraw" --resize 1280x780:1920x1080 --census -- app.py`.
A class defined in the script you run is `__main__:Window.animate`.

| What | Budget | If over |
|---|---|---|
| animation frame (30 fps, `after(33)`) | ≤ 4 ms of work, gaps steady | recolour existing items; never create or delete per frame; time-based motion |
| data redraw (once a second) | ≤ 20 ms, so no visible hitch | cache measuring and fitting; cache AA masks; fewer items |
| drag-resize, 60 steps over 1 s | done in ≤ 1.5 s, ≥ 20 fps meanwhile | coalesce `<Configure>`: rebuild at most every 50 ms plus once at the end |
| first visit to a page | ≤ 300 ms, once | fine; warm the caches only if it matters |

The fixes, with code and the numbers they bought: `references/perf-playbook.md`.

## LCARS and other starship looks

Palette, geometry (elbows, pills, header bars, end caps), type, state colours, animation language, and what makes it
read as real instead of a parody: `references/lcars-style.md`. Collect **high-resolution** references. A 300 px
product thumbnail cannot teach geometry; the agent will guess, and it shows.

## Mockups with an image model

An image model's edit endpoint can remaster your own screenshot ("same layout, crisper LCARS rendering") into a
visual target in about a minute. Use the mockup for the look (weight, glow, rounding, spacing), never for text: small
text comes out garbled. Prompt template and caveats: `references/mockups.md`.

## Checklist before you call it done

- [ ] Every page captured at the user's size and at maximized 1080p, with no errors in the app's log.
- [ ] Zoomed crops show no stair-stepped curves, no seams, no fringes, and no layout shift above 1 px against before.
- [ ] `tkprobe`:
  - steady fps at the nominal rate and no late frames;
  - redraw ≤ 20 ms;
  - drag-resize keeps up.
- [ ] Alert, idle, offline and busy states rendered and looked at; the colours of each state are distinct.
- [ ] No new dependencies; works on the stock python.org Windows Python.
- [ ] Before/after sheet, the perf table and the zoomed crops are ready for the PR.

Case study, a real console taken through this loop: `references/case-study.md`.
