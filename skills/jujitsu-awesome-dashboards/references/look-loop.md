# The look loop: run it, see it, judge it, fix it

## 1. Make the app easy to photograph

Add these to the app (they cost little and pay off on every change):

- **`--selftest [seconds]`:**
  - starts with the window titled `... (selftest)`, so captures never grab the user's real instance;
  - prints errors to stdout;
  - optionally dumps the text it drew;
  - closes itself.
- **`--page=N`** (or `--view=NAME`): open directly on a page.
- **A data seam:** read the backend through one function or URL (for example `APP_URL`, default the real
  server), so a mock can stand in.
- **Error visibility:** if a page's draw raises, show "THIS PAGE HIT AN ERROR" on the page and log the traceback. A
  silent `except: pass` hides broken pages from screenshots.

## 2. Realistic, deterministic data

Screenshots of an empty or offline UI hide most problems. Write a small mock of the backend:
- stdlib `http.server` on `127.0.0.1`;
- every field the UI reads (grep the UI for `.get("...")` on the payload);
- values that move smoothly with time (sines plus seeded noise), with the same seed giving the same story;
- **scenarios**: `busy` (default), `idle`, `alert` (every warning condition at once), `offline` (no server), plus any
  special states your UI has (loading, standby...).

Fixture files the UI reads (configs, logs, session files) are generated with **timestamps relative to now**, so
"last hour" panels have content.

Point the app at the mock with the env var, render every page in every scenario, and look at alert and offline
especially: they're the screens nobody photographs and the ones with broken colours.

## 3. Capture right

- `win_capture.py --title "(selftest)" --size 1280x780` sets the **client** area exactly, then grabs with
  PrintWindow, which works behind other windows.
- **Capture sizes:**
  - the user's usual size;
  - maximized on 1080p (1920×1009 client is typical with a taskbar);
  - the minimum size the app allows.
- **Never judge from a downscaled image.** A 1920 px window shrunk to 1100 px (chat apps and screenshot tools do
  this) looks blurry whatever you do. Zoom crops at 2–3× are the truth.

## 4. Look like a reviewer

For each page, zoom 3× into:
- curves (elbows, pill ends, dots, rings);
- diagonals (chart lines, triangles);
- every place a rounded piece meets a straight one;
- small text.

Compare with the same crop before and after (`imgtool.py pairs`). Note:
- **jaggies:** a stair-step on a curve;
- **seams:** a 1 px notch or line where pieces meet;
- **drift:** a gap between neighbours changed, or something moved;
- **weight:** lines or outlines that look thinner or fainter than before;
- **fringes:** a halo of the wrong colour.

Then hand the shots, but **not your opinion**, to a fresh agent for an independent audit. It returns a prioritized
list with coordinates and crops. Fix what's real; keep what's intentional and say so.

## 5. Keep the receipts

```
shots/before/<WxH>/pageNN.png
shots/after/<WxH>/pageNN.png
shots/compare/<WxH>/...      # imgtool pairs before/<WxH> after/<WxH> (one size folder per run) / sheet
perf.md                      # tkprobe numbers, before vs after
```
They make the PR self-explanatory: the reviewer sees the difference and the cost in one minute.
