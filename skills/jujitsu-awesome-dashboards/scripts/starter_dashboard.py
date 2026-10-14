"""starter_dashboard: a small LCARS-style telemetry panel that shows every pattern of the skill in one file.

    python starter_dashboard.py                    # run it (fake telemetry)
    python starter_dashboard.py --selftest 8       # run 8 s, print timings, close (for agents and CI)
    python starter_dashboard.py --size 1280x780 --seed 3

Patterns (search for them):  [AA] anti-aliased curves via aa_canvas.Painter · [LAYERS] static frame built on resize,
dynamic layer redrawn once a second, animation recolours a few items 30x a second · [MEASURE] cached text
measuring/fitting · [RESIZE] coalesced rebuilds · [TIME] animation driven by elapsed time, not frame count ·
[FONTS] Windows font with fallbacks · [DPI] DPI awareness before Tk() · [DATA] deterministic fake feed.
Standard library only; put aa_canvas.py next to it.
"""
from __future__ import annotations

import argparse
import ctypes
import math
import random
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

from aa_canvas import Painter

BG, ORANGE, PEACH, TAN, GOLD = "#000000", "#FF9900", "#FF9966", "#FFCC99", "#FFCC66"
LILAC, VIOLET, BLUE, SKY = "#CC99CC", "#9977DD", "#9999FF", "#99CCFF"
RED, GREEN, INK, MUTED, GRID = "#FF5544", "#66CC99", "#FFCC99", "#8f8576", "#1c1c24"


def mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


class Feed:
    """[DATA] Deterministic fake telemetry: same seed, same story. Swap for your real source."""

    def __init__(self, seed: int):
        self.rnd = random.Random(seed)
        self.t0 = time.time()
        self.hist = {k: [] for k in ("load", "temp", "rate")}

    def sample(self) -> dict:
        t = time.time() - self.t0
        s = {"load": 55 + 35 * math.sin(t / 7) + self.rnd.uniform(-6, 6),
             "temp": 62 + 12 * math.sin(t / 19) + self.rnd.uniform(-1, 1),
             "rate": 38 + 4 * math.sin(t / 3) + self.rnd.uniform(-2, 2),
             "mem": 0.7 + 0.2 * math.sin(t / 11)}
        for k in self.hist:
            self.hist[k] = (self.hist[k] + [s[k]])[-90:]
        return s


class Panel:
    def __init__(self, feed: Feed, size: str, selftest: bool = False):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # [DPI] before the first window, or Windows blurs it
        except Exception:
            pass
        self.feed = feed
        self.root = tk.Tk()
        # a test run gets its own title, so captures never grab the user's real window
        self.root.title("Starter Dashboard" + (" (selftest)" if selftest else ""))
        self.root.geometry(size)
        self.root.configure(bg=BG)
        fams = set(tkfont.families(self.root))                  # [FONTS] first one installed wins
        cond = next((f for f in ("Bahnschrift SemiBold Condensed", "Antonio", "Barlow Condensed SemiBold",
                                 "Segoe UI Semibold") if f in fams), "TkDefaultFont")
        light = next((f for f in ("Bahnschrift Condensed", "Barlow Condensed", "Segoe UI") if f in fams),
                     "TkDefaultFont")
        self.f_title, self.f_head, self.f_lab = (cond, 30), (cond, 15), (cond, 12)
        self.f_small, self.f_big = (light, 11), (cond, 22)
        self.c = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.aa = Painter(self.c)                               # [AA]
        self.W = self.H = 0
        self._resize_job, self._built_at = None, 0.0
        self._measured: dict = {}
        self._fitted: dict = {}
        self.cells: list = []
        self.data: dict = {}
        self.last_anim = time.time()
        self.phase = 0.0
        self.c.bind("<Configure>", self.on_resize)

    # ---------------------------------------------------------------- [MEASURE] text
    def measure(self, s, font) -> int:
        key = (s, font)
        w = self._measured.get(key)
        if w is None:
            if len(self._measured) > 20000:
                self._measured.clear()
            w = self._measured[key] = tkfont.Font(root=self.root, font=font).measure(s)
        return w

    def fit(self, s, font, room) -> str:
        key = (s, font, room)
        if key not in self._fitted:
            out = s
            if self.measure(s, font) > room:
                lo, hi = 0, len(s)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    lo, hi = (mid, hi) if self.measure(s[:mid] + "…", font) <= room else (lo, mid - 1)
                out = s[:lo] + "…"
            self._fitted[key] = out
        return self._fitted[key]

    def text(self, x, y, s, font, fill=INK, anchor="w", tags=("dyn",)):
        return self.c.create_text(x, y, text=s, font=font, fill=fill, anchor=anchor, tags=tags)

    # ---------------------------------------------------------------- [RESIZE]
    def on_resize(self, e) -> None:
        if (e.width, e.height) == (self.W, self.H):
            return
        self.W, self.H = e.width, e.height
        if self._resize_job is None:                            # at most one rebuild per 50 ms, plus a last one
            wait = max(0, int(50 - (time.time() - self._built_at) * 1000))
            self._resize_job = self.root.after(wait, self._rebuild)

    def _rebuild(self) -> None:
        self._resize_job, self._built_at = None, time.time()
        self.build_static()
        self.draw_dynamic()

    # ---------------------------------------------------------------- [LAYERS] static frame
    def build_static(self) -> None:
        c, aa, W, H = self.c, self.aa, self.W, self.H
        c.delete("all")
        aa.prune()
        t = ("static",)
        SW, R, r, BH, top = 150, 44, 20, 22, 104
        # the elbow: straight parts are rectangles, the two curves are AA pieces on whole pixels
        c.create_rectangle(12, 12, 12 + SW, top - R, fill=ORANGE, outline="", tags=t)
        c.create_rectangle(12 + R, top - R, 12 + SW, top, fill=ORANGE, outline="", tags=t)
        c.create_rectangle(12 + SW, top - BH, W - 280, top, fill=ORANGE, outline="", tags=t)
        aa.corner(12 + R, top - R, R, 12, top - R, 12 + R, top, ORANGE, t)
        aa.corner(12 + SW + r, top - BH - r, r, 12 + SW, top - R, 12 + SW + r, top - BH, ORANGE, t, inside=False)
        x = W - 274
        for w, col in ((70, LILAC), (60, PEACH), (64, BLUE)):
            c.create_rectangle(x, top - BH, x + w, top, fill=col, outline="", tags=t)
            x += w + 6
        aa.pill(x, top - BH, W - 12, top, LILAC, t, left=False)
        self.text(W - 16, 34, "TELEMETRY · DECK 7", self.f_title, ORANGE, "e", t)
        y = 112
        for i, (name, col) in enumerate((("SYSTEMS", ORANGE), ("SENSORS", PEACH), ("POWER", SKY), ("COMMS", BLUE),
                                         ("LOG", LILAC))):
            c.create_rectangle(12, y, 12 + SW, y + 48, fill=col, outline="", tags=t)
            self.text(12 + SW - 8, y + 36, name, self.f_lab, BG, "e", t)
            self.text(20, y + 10, f"0{i + 1}", self.f_small, BG, "w", t)
            y += 53
        c.create_rectangle(12, y, 12 + SW, H - 52, fill=ORANGE, outline="", tags=t)
        aa.pill(12, H - 46, 12 + SW, H - 12, ORANGE, t)
        # the "core": a column of coils the animation recolours, an AA chamber with a glow
        cx, cy = 300, (top + H) / 2
        self.core = (cx, cy)
        aa.glow(cx - 50, cy - 30, cx + 50, cy + 30, 20, 0.5, mix(BG, SKY, 0.35), t)
        self.cells = []
        for k in range(-7, 8):
            if abs(k) < 3:                                      # leave room for the chamber and its orbit
                continue
            yy = cy + k * 22
            self.cells.append((c.create_rectangle(cx - 38, yy - 7, cx + 38, yy + 7, fill=GRID, outline="", tags=t),
                               abs(k)))
        self.chamber = aa.oval(cx - 50, cy - 30, cx + 50, cy + 30, mix(BG, SKY, 0.4), t)
        aa.oval_ring(cx - 49, cy - 29, cx + 49, cy + 29, 2.4, SKY, t)
        aa.oval_ring(cx - 80, cy - 44, cx + 80, cy + 44, 2.4, GREEN, t)
        self.header(186, 118, 404, "CORE", ORANGE)
        self.header(420, 118, W - 14, "SENSOR ARRAY · LIVE", BLUE)

    def header(self, x0, y, x1, label, color, tags=("static",)):
        self.aa.pill(x0, y, x0 + 26, y + 18, color, tags, right=False)
        self.text(x0 + 34, y + 9, label, self.f_head, color, "w", tags)
        w = self.measure(label, self.f_head)
        self.c.create_rectangle(x0 + 44 + w, y + 7, x1 - 9, y + 11, fill=color, outline="", tags=tags)
        self.aa.oval(x1 - 18, y, x1, y + 18, color, tags)

    # ---------------------------------------------------------------- [LAYERS] data, once a second
    def draw_dynamic(self) -> None:
        if not self.W:
            return
        c, aa, s = self.c, self.aa, self.data or self.feed.sample()
        c.delete("dyn")
        aa.prune()
        x0, x1 = 420, self.W - 14
        y = 160
        for name, v, top, col in (("CORE LOAD", s["load"], 100, ORANGE), ("TEMPERATURE", s["temp"], 100, PEACH),
                                  ("OUTPUT RATE", s["rate"], 60, GOLD), ("MEMORY", s["mem"] * 100, 100, LILAC)):
            self.text(x0, y, name, self.f_lab, col)
            self.text(x0 + 390, y, f"{v:5.1f}", self.f_lab, INK, "e")
            n = 30
            lit = round(max(0.0, min(1.0, v / top)) * n)
            for k in range(n):                                  # segmented bar: plain rectangles, already crisp
                c.create_rectangle(x0 + 120 + k * 7, y - 6, x0 + 125 + k * 7, y + 6,
                                   fill=col if k < lit else GRID, outline="", tags=("dyn",))
            y += 26
        gx, gy, gr = x0 + 480, 210, 46                          # gauge: 27 AA arc segments, masks cached
        frac = s["load"] / 100
        for k in range(27):
            col = (RED if frac > 0.85 else ORANGE) if k < round(frac * 27) else GRID
            aa.arc(gx, gy, gr, 225 - k * 10 - 8, 8, 9, col, ("dyn",))
        self.text(gx, gy, f"{s['load']:.0f}", self.f_big, INK, "center")
        cy0, cy1 = 290, min(self.H - 20, 470)                   # chart: AA polyline over a plain filled area
        c.create_rectangle(x0, cy0, x1, cy1, fill="#07070c", outline=GRID, tags=("dyn",))
        vals = self.feed.hist["rate"]
        if len(vals) > 1:
            pts = []
            for i, v in enumerate(vals):
                pts += [x0 + 8 + (x1 - x0 - 16) * i / 89, cy1 - 6 - (cy1 - cy0 - 30) * min(1.0, v / 60)]
            c.create_polygon(pts[0], cy1 - 1, *pts, pts[-2], cy1 - 1, fill=mix(BG, GOLD, 0.18), outline="",
                             tags=("dyn",))
            aa.line(pts, 2.25, GOLD, ("dyn",))
            aa.oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3, GOLD, ("dyn",))
        self.text(x0 + 10, cy0 + 12, "OUTPUT RATE · LAST 90 S", self.f_small, GOLD)
        note = "ALL SYSTEMS NOMINAL · SENSOR SWEEP COMPLETE · NO ANOMALIES DETECTED IN SECTOR 001"
        self.text(x0, self.H - 30, self.fit(note, self.f_small, x1 - x0), self.f_small, MUTED)
        ok = s["temp"] < 72
        lab = "CONDITION GREEN" if ok else "CONDITION YELLOW"
        w = self.measure(lab, self.f_lab) + 30
        aa.pill(self.W - 16 - w, 58, self.W - 16, 78, GREEN if ok else GOLD, ("dyn",))
        self.text(self.W - 16 - w / 2, 68, lab, self.f_lab, BG, "center")

    def refresh(self) -> None:
        self.data = self.feed.sample()
        self.draw_dynamic()
        self.root.after(1000, self.refresh)

    # ---------------------------------------------------------------- [TIME] animation, 30 a second
    def animate(self) -> None:
        now = time.time()
        self.phase += (now - self.last_anim) * (1.0 + (self.data.get("load") or 0) / 25)
        self.last_anim = now
        for item, k in self.cells:
            b = math.exp(-((k - 3 - self.phase % 5) ** 2) / 0.8)
            self.c.itemconfigure(item, fill=mix("#162a4d", "#bfe4ff", 0.15 + 0.85 * b))
        if self.cells:
            self.aa.recolor(self.chamber, mix("#162a4d", SKY, 0.45 + 0.35 * math.sin(now * 5)))
        self.root.after(33, self.animate)

    def run(self, selftest: float = 0) -> None:
        self.root.after(200, self.refresh)
        self.root.after(250, self.animate)
        if selftest:
            self.root.after(int(selftest * 1000), self.root.destroy)
        self.root.mainloop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", default="1280x780")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--selftest", type=float, default=0, help="close after N seconds and print timings")
    a = ap.parse_args()
    panel = Panel(Feed(a.seed), a.size, bool(a.selftest))
    if a.selftest:
        times = {"draw_dynamic": [], "animate": []}
        for name in times:
            fn = getattr(panel, name)

            def timed(fn=fn, name=name):
                t = time.perf_counter()
                fn()
                times[name].append((time.perf_counter() - t) * 1000)
            setattr(panel, name, timed)
    panel.run(a.selftest)
    if a.selftest:
        for name, v in times.items():
            v = sorted(v)
            if v:
                print(f"{name}: n={len(v)} median={v[len(v) // 2]:.1f} ms max={v[-1]:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
