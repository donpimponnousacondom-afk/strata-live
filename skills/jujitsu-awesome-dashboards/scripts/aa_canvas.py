"""aa_canvas: anti-aliased shapes for a tkinter Canvas, standard library only (Tk 8.6+).

Tk's canvas draws ovals, arcs, polygons and slanted lines WITHOUT anti-aliasing (Windows, X11 alike): every curve
comes out stair-stepped. This module rasterises such shapes into alpha masks from their signed distance
(coverage = 0.5 - distance), colours them into PNGs (Tk 8.6 decodes PNG natively) and places them as canvas images
at the same coordinates. Axis-aligned rectangles should stay plain canvas rectangles: they are already crisp.

    from aa_canvas import Painter
    aa = Painter(canvas)
    aa.pill(20, 20, 220, 44, "#FF9900", tags=("static",))        # rounded bar: crisp body, AA ends
    aa.oval(x0, y0, x1, y1, "#99CCFF")                           # like create_oval(fill=)
    aa.oval_ring(x0, y0, x1, y1, 2.4, "#66CC99")                 # like create_oval(outline=, width=)
    aa.arc(cx, cy, r, start, extent, width, color)               # like create_arc(style="arc")
    aa.polygon((x0, y0, x1, y1, x2, y2), color, erode=0.4)       # like create_polygon(fill=)
    aa.line(points, 2.25, color)                                 # like create_line(width=), cached by points
    aa.segment(x0, y0, x1, y1, 2, color)                         # short ticks, square ends like Tk
    aa.corner(cx, cy, r, x0, y0, x1, y1, color, inside=False)    # LCARS elbow pieces (convex / concave fillet)
    aa.glow(x0, y0, x1, y1, spread, peak, color)                 # soft halo around an oval
    aa.recolor(item, color)                                      # animation / alert flashing, cached
    canvas.delete("dyn"); aa.prune()                             # after deleting items, drop their records

Masks are cached by geometry (+ image size), photos by (geometry, colour), so redraws and colour animation cost a
lookup. Only edges are computed per pixel (8x8 tiles fully inside/outside are filled at once).
Budget guide (pure Python): a 120x76 ellipse ~1.6 ms the first time, recolour ~0.2 ms, a 120-point chart line
~5 ms (then cached while its points don't change).
"""
from __future__ import annotations

import base64
import math
import struct
import zlib
from collections import OrderedDict

import tkinter as tk

_TILE = 8
_TILE_REACH = _TILE * 0.7072 + 1.0          # a tile is all in or all out if its centre is this far from the edge


def _px(v: float) -> int:
    """Snap to a whole pixel, halves up (round() would send 0.5 and 1.5 both to 2, moving neighbours apart)."""
    return math.floor(v + 0.5)


def _q(v: float) -> float:
    """Geometry rounded to 1/8 px, so near-identical shapes share one mask."""
    return round(v * 8) / 8


# ------------------------------------------------------------ signed distances (px, negative inside)
def sd_disc(cx, cy, r):
    hyp = math.hypot

    def f(x, y):
        return hyp(x - cx, y - cy) - r
    return f


def sd_ellipse(cx, cy, a, b):
    if abs(a - b) < 1e-6:
        return sd_disc(cx, cy, a)
    hyp, a2, b2 = math.hypot, a * a, b * b

    def f(x, y):
        px, py = x - cx, y - cy
        k0 = hyp(px / a, py / b)
        if k0 < 1e-9:
            return -min(a, b)
        return k0 * (k0 - 1.0) / hyp(px / a2, py / b2)
    return f


def sd_capsule(ax, ay, bx, by, r):
    """A thick segment with round ends (a pill, a round-capped line)."""
    hyp = math.hypot
    ex, ey = bx - ax, by - ay
    ll = ex * ex + ey * ey

    def f(x, y):
        wx, wy = x - ax, y - ay
        t = 0.0 if ll == 0 else (wx * ex + wy * ey) / ll
        t = 0.0 if t < 0 else 1.0 if t > 1 else t
        return hyp(wx - ex * t, wy - ey * t) - r
    return f


def sd_polygon(pts):
    n = len(pts)

    def f(x, y):
        d = (x - pts[0][0]) ** 2 + (y - pts[0][1]) ** 2
        s = 1.0
        j = n - 1
        for i in range(n):
            vix, viy = pts[i]
            vjx, vjy = pts[j]
            ex, ey = vjx - vix, vjy - viy
            wx, wy = x - vix, y - viy
            ll = ex * ex + ey * ey
            t = 0.0 if ll == 0 else (wx * ex + wy * ey) / ll
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
            bx, by = wx - ex * t, wy - ey * t
            d = min(d, bx * bx + by * by)
            c1, c2, c3 = y >= viy, y < vjy, ex * wy > ey * wx
            if (c1 and c2 and c3) or not (c1 or c2 or c3):
                s = -s
            j = i
        return s * math.sqrt(d)
    return f


def sd_arc(cx, cy, r, width, start, extent):
    """Tk's style="arc": a band `width` wide centred on radius r, from `start` degrees (counter-clockwise from 3
    o'clock) over `extent` degrees, with square ends."""
    if extent < 0:
        start, extent = start + extent, -extent
    hw, mid, half = width / 2.0, start + extent / 2.0, extent / 2.0
    hyp, atan2, sin, deg, rad = math.hypot, math.atan2, math.sin, math.degrees, math.radians

    def f(x, y):
        px, py = x - cx, cy - y
        ll = hyp(px, py)
        dr = abs(ll - r) - hw
        if extent >= 360:
            return dr
        over = abs((deg(atan2(py, px)) - mid + 180.0) % 360.0 - 180.0) - half
        over = -90.0 if over < -90 else 90.0 if over > 90 else over
        da = ll * sin(rad(over))
        return dr if dr > da else da
    return f


def ring(sd, width):
    hw = width / 2.0

    def f(x, y):
        return abs(sd(x, y)) - hw
    return f


def outside(sd):
    def f(x, y):
        return -sd(x, y)
    return f


# ------------------------------------------------------------ rasterising
def raster(w: int, h: int, sd) -> bytearray:
    """Coverage (0-255) of a shape over a w x h grid; pixel (i, j) has its centre at (i + 0.5, j + 0.5)."""
    a = bytearray(w * h)
    full = b"\xff" * _TILE
    for ty in range(0, h, _TILE):
        th = min(_TILE, h - ty)
        for tx in range(0, w, _TILE):
            tw = min(_TILE, w - tx)
            d = sd(tx + tw / 2.0, ty + th / 2.0)
            if d > _TILE_REACH:
                continue
            if d < -_TILE_REACH:
                for j in range(ty, ty + th):
                    a[j * w + tx:j * w + tx + tw] = full[:tw]
                continue
            for j in range(ty, ty + th):
                y = j + 0.5
                row = j * w
                for i in range(tx, tx + tw):
                    c = 0.5 - sd(i + 0.5, y)
                    if c > 0:
                        a[row + i] = 255 if c >= 1 else int(c * 255 + 0.5)
    return a


def raster_glow(w: int, h: int, sd, spread: float, peak: float) -> bytearray:
    """A soft halo: full `peak` inside the shape, fading out over `spread` px."""
    a = bytearray(w * h)
    for j in range(h):
        y = j + 0.5
        row = j * w
        for i in range(w):
            d = sd(i + 0.5, y)
            if d <= 0:
                v = peak
            elif d < spread:
                v = peak * (1.0 - d / spread) ** 2
            else:
                continue
            a[row + i] = int(v * 255 + 0.5)
    return a


def stroke(w: int, h: int, pts: list, width: float) -> bytearray:
    """A polyline `width` px wide with round joins, local coordinates. Each segment is walked along its longer
    axis and only a thin band around it is measured, so a long chart line costs about its length."""
    a = bytearray(w * h)
    hw = width / 2.0
    hyp, floor, ceil = math.hypot, math.floor, math.ceil
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        ex, ey = x2 - x1, y2 - y1
        ll = ex * ex + ey * ey
        steep = abs(ey) > abs(ex)
        if steep:
            lo, hi, s0, s1 = min(y1, y2), max(y1, y2), y1, y2
            slope = ex / ey if ey else 0.0
            n_main, n_cross = h, w
        else:
            lo, hi, s0, s1 = min(x1, x2), max(x1, x2), x1, x2
            slope = ey / ex if ex else 0.0
            n_main, n_cross = w, h
        band = hw * math.sqrt(1 + slope * slope) + 1.5
        for m in range(max(0, floor(lo - hw - 1)), min(n_main, ceil(hi + hw + 1))):
            mc = m + 0.5
            if steep:
                t = (mc - y1) / ey if ey else 0.0
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                cc = x1 + ex * t
            else:
                t = (mc - x1) / ex if ex else 0.0
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                cc = y1 + ey * t
            for k in range(max(0, floor(cc - band)), min(n_cross, ceil(cc + band) + 1)):
                kc = k + 0.5
                px, py = (kc, mc) if steep else (mc, kc)
                wx, wy = px - x1, py - y1
                t = 0.0 if ll == 0 else (wx * ex + wy * ey) / ll
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                c = 0.5 - (hyp(wx - ex * t, wy - ey * t) - hw)
                if c > 0:
                    v = 255 if c >= 1 else int(c * 255 + 0.5)
                    idx = m * w + k if steep else k * w + m
                    if v > a[idx]:
                        a[idx] = v
    return a


def png(w: int, h: int, rgb: tuple, alpha: bytes) -> bytes:
    """An RGBA PNG of one colour with the given coverage."""
    n = w * h
    px = bytearray(4 * n)
    px[0::4] = bytes((rgb[0],)) * n
    px[1::4] = bytes((rgb[1],)) * n
    px[2::4] = bytes((rgb[2],)) * n
    px[3::4] = alpha
    st = 4 * w
    raw = b"".join(b"\x00" + px[k:k + st] for k in range(0, 4 * n, st))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ------------------------------------------------------------ the painter
class Painter:
    """Places anti-aliased shapes on a canvas. Every method takes canvas coordinates, like the canvas' own create_*
    calls, and returns the new item(s). `recolor(item, colour)` restyles an item later (animation, red alert)."""

    def __init__(self, canvas: tk.Canvas, max_masks: int = 1500, max_photos: int = 500):
        self.c = canvas
        self.masks: OrderedDict = OrderedDict()
        self.photos: OrderedDict = OrderedDict()
        self.items: dict = {}                   # item -> [mask key, colour, photo, w, h, make]; keeps the photo alive
        self.max_masks, self.max_photos = max_masks, max_photos
        self._rgb: dict = {}

    # -- bookkeeping
    def rgb(self, color: str) -> tuple:
        v = self._rgb.get(color)
        if v is None:
            if color.startswith("#") and len(color) == 7:
                v = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
            else:
                v = tuple(x // 257 for x in self.c.winfo_rgb(color))
            self._rgb[color] = v
        return v

    def _mask(self, key, w, h, make):
        m = self.masks.get(key)
        if m is None:
            m = self.masks[key] = make(w, h)
            if len(self.masks) > self.max_masks:
                self.masks.popitem(last=False)
        else:
            self.masks.move_to_end(key)
        return m

    def _photo(self, key, w, h, alpha, color):
        pk = (key, color)
        p = self.photos.get(pk)
        if p is None:
            p = self.photos[pk] = self.make_photo(w, h, alpha, color)
            if len(self.photos) > self.max_photos:
                self.photos.popitem(last=False)
        else:
            self.photos.move_to_end(pk)
        return p

    def make_photo(self, w, h, alpha, color):
        data = base64.b64encode(png(w, h, self.rgb(color), alpha)).decode("ascii")
        return tk.PhotoImage(master=self.c, data=data, format="png")

    def _emit(self, ix, iy, w, h, key, make, color, tags):
        if w <= 0 or h <= 0:
            return None
        key = key + (w, h)                       # the same shape can land on a grid one pixel bigger
        alpha = self._mask(key, w, h, make)
        photo = self._photo(key, w, h, alpha, color)
        item = self.c.create_image(ix, iy, image=photo, anchor="nw", tags=tags)
        self.items[item] = [key, color, photo, w, h, make]
        return item

    def recolor(self, item, color) -> None:
        rec = self.items.get(item)
        if rec is None:
            self.c.itemconfigure(item, fill=color)
            return
        if rec[1] == color:
            return
        key, _, _, w, h, make = rec
        alpha = self._mask(key, w, h, make)
        rec[1], rec[2] = color, self._photo(key, w, h, alpha, color)
        self.c.itemconfigure(item, image=rec[2])

    def color_of(self, item):
        rec = self.items.get(item)
        return rec[1] if rec else None

    def prune(self) -> None:
        """Forget items the canvas has deleted (call after canvas.delete)."""
        t = self.c.type
        for item in [i for i in self.items if not t(i)]:
            del self.items[item]

    @staticmethod
    def _box(x0, y0, x1, y1, pad):
        ix, iy = math.floor(x0) - pad, math.floor(y0) - pad
        return ix, iy, math.ceil(x1) + pad - ix, math.ceil(y1) + pad - iy

    # -- shapes
    def oval(self, x0, y0, x1, y1, color, tags=()):
        """A filled oval in the bounding box, like create_oval(fill=...)."""
        ix, iy, w, h = self._box(x0, y0, x1, y1, 1)
        cx, cy, a, b = _q((x0 + x1) / 2 - ix), _q((y0 + y1) / 2 - iy), _q((x1 - x0) / 2), _q((y1 - y0) / 2)
        return self._emit(ix, iy, w, h, ("oval", cx, cy, a, b), lambda w_, h_: raster(w_, h_, sd_ellipse(cx, cy, a, b)),
                          color, tags)

    def oval_ring(self, x0, y0, x1, y1, width, color, tags=()):
        """An oval's outline, like create_oval(outline=..., width=...): a stroke centred on the oval."""
        pad = math.ceil(width / 2) + 1
        ix, iy, w, h = self._box(x0, y0, x1, y1, pad)
        cx, cy, a, b = _q((x0 + x1) / 2 - ix), _q((y0 + y1) / 2 - iy), _q((x1 - x0) / 2), _q((y1 - y0) / 2)
        return self._emit(ix, iy, w, h, ("ovalring", cx, cy, a, b, width),
                          lambda w_, h_: raster(w_, h_, ring(sd_ellipse(cx, cy, a, b), width)), color, tags)

    def arc(self, cx, cy, r, start, extent, width, color, tags=()):
        """create_arc(style="arc") on a circle of radius r: only the band's own bounding box is drawn."""
        hw = width / 2.0
        s, e = (start + extent, -extent) if extent < 0 else (start, extent)
        angs = [s, s + e] + [k * 90.0 for k in range(-4, 9) if s < k * 90.0 < s + e]
        xs, ys = [], []
        for ang in angs:
            ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            for rr in (r - hw, r + hw):
                xs.append(cx + rr * ca)
                ys.append(cy - rr * sa)
        ix, iy, w, h = self._box(min(xs), min(ys), max(xs), max(ys), 1)
        lx, ly = _q(cx - ix), _q(cy - iy)
        r_, s_, e_ = _q(r), round(s, 2), round(e, 2)
        return self._emit(ix, iy, w, h, ("arc", lx, ly, r_, width, s_, e_),
                          lambda w_, h_: raster(w_, h_, sd_arc(lx, ly, r_, width, s_, e_)), color, tags)

    def polygon(self, points, color, tags=(), erode=0.0):
        """A filled polygon, like create_polygon(x0, y0, x1, y1, ...). `erode` pulls the edges in (px): a small
        anti-aliased shape reads about half a pixel bigger than Tk's hard-edged one."""
        xs, ys = points[0::2], points[1::2]
        ix, iy, w, h = self._box(min(xs), min(ys), max(xs), max(ys), 1)
        pts = tuple((_q(x - ix), _q(y - iy)) for x, y in zip(xs, ys))

        def make(w_, h_):
            sd = sd_polygon(pts)
            return raster(w_, h_, (lambda x, y: sd(x, y) + erode) if erode else sd)
        return self._emit(ix, iy, w, h, ("poly", pts, erode), make, color, tags)

    def segment(self, x0, y0, x1, y1, width, color, tags=()):
        """A short straight line (ticks), as long as Tk's (square ends); cached by geometry."""
        ll = math.hypot(x1 - x0, y1 - y0)
        if ll > width:                          # round ends reach width/2 past each end: start them that far in
            k = width / 2 / ll
            x0, y0, x1, y1 = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k, x1 - (x1 - x0) * k, y1 - (y1 - y0) * k
        pad = math.ceil(width / 2) + 1
        ix, iy, w, h = self._box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), pad)
        a = (_q(x0 - ix), _q(y0 - iy), _q(x1 - ix), _q(y1 - iy))
        return self._emit(ix, iy, w, h, ("seg",) + a + (width,),
                          lambda w_, h_: raster(w_, h_, sd_capsule(*a, width / 2.0)), color, tags)

    def line(self, points, width, color, tags=()):
        """A polyline, like create_line(x0, y0, x1, y1, ..., width=...). Cached by its points, so a chart whose
        data has not changed costs a lookup."""
        xs, ys = points[0::2], points[1::2]
        pad = math.ceil(width / 2) + 2
        ix, iy, w, h = self._box(min(xs), min(ys), max(xs), max(ys), pad)
        pts = tuple((_q(x - ix), _q(y - iy)) for x, y in zip(xs, ys))
        return self._emit(ix, iy, w, h, ("line", width, pts), lambda w_, h_: stroke(w_, h_, pts, width), color, tags)

    def pill(self, x0, y0, x1, y1, color, tags=(), left=True, right=True):
        """A rounded bar (LCARS pill): a crisp rectangle body and two anti-aliased round ends. Returns a list
        of items (unlike the single-item shapes): recolour each one."""
        x0, y0, x1, y1 = _px(x0), _px(y0), _px(x1), _px(y1)
        r = (y1 - y0) / 2.0
        if x1 - x0 < y1 - y0:                   # narrower than tall: one oval
            return [self.oval(x0, y0, x1, y1, color, tags)]
        # the round ends stop half a pixel short of the box, as Tk's ovals do, so gaps between pills stay the same
        a_end, b_end = x0 + 0.5 + r, x1 - 0.5 - r
        cl, cr = (math.ceil(a_end) if left else x0), (math.floor(b_end) if right else x1)
        ids = []
        if cr > cl:
            ids.append(self.c.create_rectangle(cl, y0, cr, y1, fill=color, outline="", tags=tags))
        h = y1 - y0
        for on, ax, w in ((left, x0, cl - x0), (right, cr, x1 - cr)):
            if not on or w <= 0:
                continue
            # the whole pill's distance, drawn only over this end, so the end meets the body without a seam
            a, b = _q(a_end - ax), _q(b_end - ax)
            key = ("cap", a, b, r, w, h)
            ids.append(self._emit(ax, y0, w, h, key, lambda w_, h_: raster(w_, h_, sd_capsule(a, r, b, r, r)),
                                  color, tags))
        return ids

    def corner(self, cx, cy, r, x0, y0, x1, y1, color, tags=(), inside=True):
        """The part of the box (x0, y0, x1, y1) inside (or, for a concave fillet, outside) the circle (cx, cy, r).
        The box should be whole pixels, so it meets the rectangles around it without a seam."""
        x0, y0, x1, y1 = _px(x0), _px(y0), _px(x1), _px(y1)
        lx, ly = _q(cx - x0), _q(cy - y0)
        sd = (lambda: sd_disc(lx, ly, r)) if inside else (lambda: outside(sd_disc(lx, ly, r)))
        return self._emit(x0, y0, x1 - x0, y1 - y0, ("corner", lx, ly, r, x1 - x0, y1 - y0, inside),
                          lambda w_, h_: raster(w_, h_, sd()), color, tags)

    def glow(self, x0, y0, x1, y1, spread, peak, color, tags=()):
        """A soft halo around an oval, fading out over `spread` px (it adds light; draw it under the oval)."""
        pad = math.ceil(spread) + 1
        ix, iy, w, h = self._box(x0, y0, x1, y1, pad)
        cx, cy, a, b = _q((x0 + x1) / 2 - ix), _q((y0 + y1) / 2 - iy), _q((x1 - x0) / 2), _q((y1 - y0) / 2)
        return self._emit(ix, iy, w, h, ("glow", cx, cy, a, b, spread, peak),
                          lambda w_, h_: raster_glow(w_, h_, sd_ellipse(cx, cy, a, b), spread, peak), color, tags)
