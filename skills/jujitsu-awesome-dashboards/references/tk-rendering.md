# Tk rendering: what's jagged, why, and the fixes

## What Tk anti-aliases and what it doesn't (Windows, Tk 8.6)

| Item | Anti-aliased? | Do |
|---|---|---|
| `create_text` | yes (ClearType / GDI) | keep |
| `create_rectangle` on whole pixels | n/a, already crisp | keep |
| `create_line`, horizontal or vertical | crisp | keep (dashes fine) |
| `create_line`, slanted (charts, ticks) | **no** | `aa.line` / `aa.segment` |
| `create_oval`, `create_arc` | **no** | `aa.oval`, `aa.oval_ring`, `aa.arc` |
| `create_polygon` with slanted edges | **no** | `aa.polygon` |
| `PhotoImage` with an alpha channel on a canvas | **blended** | this is what `aa_canvas` uses |

Tk 8.6 decodes PNG itself, so `tk.PhotoImage(data=base64_png, format="png")` needs no Pillow.

## How `aa_canvas` works (so you can extend it)

1. Each shape has a **signed distance function** in pixels: negative inside, positive outside. Coverage of a pixel
   is `clamp(0.5 - d, 0, 1)` at its centre `(i + .5, j + .5)`. That's a box-filter estimate, good for edges.
   - Combine shapes with `min` for union and `max` for intersection.
   - `abs(d) - w/2` turns a shape into its outline.
   - `d + k` erodes a shape by k px.
2. **Tiles:** an 8×8 tile whose centre is more than about 6.7 px from the edge is all-in or all-out and is filled
   with one slice. Only the band around the edges is computed per pixel, so cost scales with perimeter, not area.
3. **Colour:** an RGBA buffer is built with slice assignment (`px[3::4] = alpha`), at C speed. Then the PNG is
   assembled with `zlib` and `struct`.
4. **Caches:**
   - masks by geometry, rounded to 1/8 px, **plus image size**, because the same shape can land on a grid one pixel
     bigger;
   - photos by (mask, colour), in an LRU.
   - Each canvas item keeps a reference to its `PhotoImage`, because Tk drops an image whose Python object is
     garbage-collected and the item turns blank.
5. **Chart lines** are walked along their long axis, measuring only a thin band, so cost is about their length. They
   are cached by their points, so an unchanged chart costs a lookup.

Measured, in pure Python on a desktop CPU:
- 120×76 ellipse: about 1.6 ms the first time;
- recolouring it: about 0.2 ms;
- a 120-point 560×120 chart line: about 5 ms;
- an elbow corner: under 1 ms.

## Pitfalls we hit, and what fixed them

- **A cached mask applied to a different-size image:** "png size mismatch". Put `(w, h)` in every cache key.
- **Seams between an AA cap and a rectangle body:** make the cut at an integer x, and raster the **whole** shape's
  distance over the cap tile (not just a disc), so the cap continues the body exactly.
- **Gaps between neighbouring pills shrank by 1 px:** an AA end covers the edge pixel Tk left empty. Pull the tips in
  0.5 px.
- **Outlines looked thinner:** Tk's 2 px outline sits inside the box and is solid. Use 2.4 px, centred 1 px inside.
- **Charts looked lighter:** about 11% less ink. A 2.25 px AA line matches a 2 px aliased one.
- **Ticks got longer:** round ends add width/2 at each end. Shorten by that, which gives Tk's butt ends.
- **Alert flashing:** code that did `itemconfigure(item, fill=RED)` on a list of shapes breaks on image items. Route
  colour changes through `aa.recolor`, which falls back to `fill=` for plain items. Remember original colours with
  `aa.color_of(item) or canvas.itemcget(item, "fill")`.
- **Memory:** recolouring with continuously varying colours (glows) creates new photos. The LRU (about 500) bounds
  it. Don't animate dozens of AA items per frame with unique colours; animate plain rectangles, or quantise colours.

## Glow and depth without a layout change

A soft `aa.glow` under a key element, recoloured with its state, adds the "lit instrument" feel that mockups show.
Keep the peak around 0.5 and the spread about 20 px, and put it under everything else in that element. Don't glow
text, and don't glow everything: one or two focal points per screen.

## Fonts and DPI

- `SetProcessDpiAwareness(1)` before `Tk()`. Then Tk's `tk scaling` follows the monitor DPI: point sizes grow on
  125/150% screens while canvas pixel geometry doesn't.
  - Either size fonts in **negative sizes (pixels)** so they track the geometry;
  - or scale the geometry by `root.winfo_fpixels("1i") / 96`.
  - Test at 100% and 150%.
- Check fonts with `tkfont.families()`. A missing family silently falls back to a default that is often wider, and
  labels overflow.
- Memoize `measure` and `fit` (see the perf playbook).

## Layers that keep redraws cheap

- **static:** frame, elbows, sidebar, headers, the instrument's skeleton. Built on resize and page change only.
- **dyn:** values, bars, charts, tables. `delete("dyn")` and redraw once a second.
- **animated:** a handful of long-lived items whose colours or coordinates change about 30 times a second
  (`itemconfigure` / `coords` / `aa.recolor`). Never create items per frame.
