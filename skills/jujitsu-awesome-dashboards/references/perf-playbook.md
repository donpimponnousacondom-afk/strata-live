# Perf playbook: make a Tk dashboard feel smooth

## Measure, don't guess

```bat
python scripts\tkprobe.py --frame "ui:Window.animate" --methods "ui:Window.redraw,ui:Window.build" ^
       --resize 1280x780:1920x1080 --census --seconds 10 -- app.py --selftest-flags
python scripts\tkprobe.py --profile "ui:Window.redraw" --seconds 8 -- app.py
```

- **frames:** `fps`, the median gap (should equal your `after()` interval), and `late(>N ms)`: gaps longer than
  1.5× the median, or the median plus one Windows timer tick (~16 ms), whichever is more. Any late frame is a
  visible stutter.
- **methods:** median, p95 and max per call. A 60 ms once-a-second redraw shows as one late frame per second.
- **resize:** how long the app took to work through a 1-second drag. More than 1.5 s means the window lags behind
  the mouse.
- **census:** canvas items alive. Thousands per redraw means the cost is item churn.
- **profile:** top functions by own time. Look for `tkapp.call` (Tk round trips), `measure`, and your rasterisers.

`tkprobe` patches classes by import path (`module:Class.method`). For a class defined in the script itself, use
`__main__:Class.method`; it's patched when the app creates its Tk root. Relative paths in the app's own arguments
resolve from your working directory.

## The usual culprits, in the order we found them

### 1. Text measuring in the redraw (the big one)
`Font.measure()` is a Tk round trip. Truncation with a binary search (`fit`) calls it about ten times per label, and
a once-a-second redraw repeats it for the same strings.

```python
def measure(self, s, font):
    key = (s, font)
    w = self._measured.get(key)
    if w is None:
        if len(self._measured) > 20000:
            self._measured.clear()
        w = self._measured[key] = tkfont.Font(root=self.root, font=font).measure(s)   # or your own Font cache
    return w
# fit(): same idea, keyed by (s, font, room); inside its binary search, call the cached measure()
```
On a real 10-page console the median redraw went from 63 to 18 ms (overview page), from 71 to 9 ms (sliders) and
from 62 to 7 ms (event log). The once-a-second hitch disappeared.

### 2. Resize rebuilds queued per event
Every `<Configure>` during a drag ran a full rebuild (about 80 ms) while events arrived every few ms. A 1-second drag
took 4–5 s to catch up, at about 11 fps.

```python
def on_resize(self, e):
    if (e.width, e.height) == (self.W, self.H):
        return
    self.W, self.H = e.width, e.height
    if self._resize_job is None:      # at most one rebuild per 50 ms, and one after the last event
        wait = max(0, int(50 - (time.time() - self._built_at) * 1000))
        self._resize_job = self.root.after(wait, self._rebuild)

def _rebuild(self):
    self._resize_job, self._built_at = None, time.time()
    self.build_static()
    self.draw_dynamic()
```
Result: the same drag took 1.35–1.45 s, at 21–22 fps meanwhile, settling 30–50 ms after the last event.

### 3. Animation rate and style
- Drive motion by **elapsed time** (`phase += speed * dt`), never by frame count. Then the frame rate can change
  without changing speeds.
- Once a frame costs under about 4 ms, 30 fps (`after(33)`) looks clearly smoother than 20 fps.
- Animate by **changing** a few items (`itemconfigure`, `coords`, `aa.recolor`), never by deleting and creating.

### 4. Anti-aliasing cost
- Cache masks by geometry and photos by colour.
- Cache chart lines by their points: a chart whose data didn't change costs a lookup.
- Live traces that change every second cost about 5 ms per line. That's fine once a second, not per frame.

### 5. Item count
Hundreds of tiny rectangles (segmented bars, sliders) are fine at 1 Hz. If a page goes past about 2,000 items, merge
segments (one image per bar) or draw fewer, wider segments.

## Before / after on a real console (1920×1009)

| | before | after |
|---|---|---|
| redraw per second, average of 10 pages | 30.2 ms (worst page 62) | 12.7 ms (worst 46, first visit only) |
| animation | 18.2 fps, 1 hitch per second | 27.4 fps target 30, no late frames in steady state |
| 61-step drag-resize | 4927 ms, 11.7 fps | 1456 ms, 21 fps |
