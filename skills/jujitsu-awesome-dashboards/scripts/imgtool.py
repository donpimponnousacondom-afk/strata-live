"""imgtool: crop / zoom / stack / contact-sheet PNGs with nothing but tkinter (Tk 8.6 reads and writes PNG).

For looking at your own screenshots closely: an agent judging a UI must zoom into edges, compare before/after at
the same spot, and see all pages at once.

    python imgtool.py zoom shot.png out.png --crop 0,0,400,240 --factor 3      # nearest-neighbour: see the pixels
    python imgtool.py stack out.png before.png after.png                        # side by side (--vertical: on top)
    python imgtool.py sheet out.png --cols 3 --shrink 2 page01.png page02.png ...  # all pages in one picture
    python imgtool.py pairs out_dir before_dir after_dir --crop 0,0,700,330 --factor 2   # same crop of every page

Shrinking is by whole factors (Tk's subsample: it drops pixels, so a sheet is for the overview only; judge edges
on zoomed crops), zooming by whole factors (nearest neighbour). Output is PNG.
"""
from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk

_root = None


def _tk():
    global _root
    if _root is None:
        _root = tk.Tk()
        _root.withdraw()
    return _root


def load(path):
    return tk.PhotoImage(master=_tk(), file=path)


def blank(w, h, color="#111111"):
    img = tk.PhotoImage(master=_tk(), width=w, height=h)
    img.put(color, to=(0, 0, w, h))
    return img


def paste(dst, src, x, y, crop=None, zoom=1, shrink=1):
    args = [dst, "copy", src]
    if crop:
        cx, cy, cw, ch = crop
        args += ["-from", cx, cy, cx + cw, cy + ch]
    args += ["-to", x, y]
    if zoom > 1:
        args += ["-zoom", zoom]
    if shrink > 1:
        args += ["-subsample", shrink]
    dst.tk.call(*args)


def clamp(src, crop):
    """A crop kept inside the image (screenshots of different sizes can share one --crop)."""
    if not crop:
        return None
    x, y, w, h = crop
    x, y = max(0, min(x, src.width() - 1)), max(0, min(y, src.height() - 1))
    return x, y, max(1, min(w, src.width() - x)), max(1, min(h, src.height() - y))


def zoomed(src, crop=None, factor=1):
    crop = clamp(src, crop)
    cw, ch = (crop[2], crop[3]) if crop else (src.width(), src.height())
    out = tk.PhotoImage(master=_tk(), width=cw * factor, height=ch * factor)
    paste(out, src, 0, 0, crop, zoom=factor)
    return out


def stack(images, vertical=False, gap=8):
    if vertical:
        w, h = max(i.width() for i in images), sum(i.height() for i in images) + gap * (len(images) - 1)
    else:
        w, h = sum(i.width() for i in images) + gap * (len(images) - 1), max(i.height() for i in images)
    out = blank(w, h)
    pos = 0
    for img in images:
        paste(out, img, 0 if vertical else pos, pos if vertical else 0)
        pos += (img.height() if vertical else img.width()) + gap
    return out


def sheet(images, cols=3, shrink=2, gap=8):
    cw = max(i.width() for i in images) // shrink
    ch = max(i.height() for i in images) // shrink
    rows = (len(images) + cols - 1) // cols
    out = blank(cols * cw + (cols - 1) * gap, rows * ch + (rows - 1) * gap)
    for k, img in enumerate(images):
        paste(out, img, (k % cols) * (cw + gap), (k // cols) * (ch + gap), shrink=shrink)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    z = sub.add_parser("zoom")
    z.add_argument("src")
    z.add_argument("out")
    z.add_argument("--crop")
    z.add_argument("--factor", type=int, default=3)
    s = sub.add_parser("stack")
    s.add_argument("out")
    s.add_argument("srcs", nargs="+")
    s.add_argument("--vertical", action="store_true")
    t = sub.add_parser("sheet")
    t.add_argument("out")
    t.add_argument("srcs", nargs="+")
    t.add_argument("--cols", type=int, default=3)
    t.add_argument("--shrink", type=int, default=2)
    p = sub.add_parser("pairs")
    p.add_argument("out_dir")
    p.add_argument("before_dir")
    p.add_argument("after_dir")
    p.add_argument("--crop")
    p.add_argument("--factor", type=int, default=1)
    p.add_argument("--vertical", action="store_true")
    a = ap.parse_args()
    crop = tuple(int(v) for v in a.crop.split(",")) if getattr(a, "crop", None) else None
    if a.cmd == "zoom":
        zoomed(load(a.src), crop, a.factor).write(a.out, format="png")
    elif a.cmd == "stack":
        stack([load(f) for f in a.srcs], a.vertical).write(a.out, format="png")
    elif a.cmd == "sheet":
        sheet([load(f) for f in a.srcs], a.cols, a.shrink).write(a.out, format="png")
    else:
        os.makedirs(a.out_dir, exist_ok=True)
        for name in sorted(os.listdir(a.before_dir)):
            after = os.path.join(a.after_dir, name)
            if name.lower().endswith(".png") and os.path.exists(after):
                pair = [zoomed(load(os.path.join(d, name)), crop, a.factor) for d in (a.before_dir, a.after_dir)]
                stack(pair, a.vertical).write(os.path.join(a.out_dir, name), format="png")
                print(os.path.join(a.out_dir, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
