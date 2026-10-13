"""Strata Engineering: the console. Strata is the warp core; the sidebar's buttons are the engineering departments.

How Strata maps onto the core (also explained on the WARP CORE page):
  matter injector     -> the prompt being read (prompt intake)
  antimatter injector -> experts streamed to the GPU over PCIe when the card doesn't hold them
  warp coils          -> the experts: lit by where the last request's experts came from (cache, RAM, PCIe)
  dilithium chamber   -> the GPU, where the experts' math happens
  containment field   -> free VRAM (Strata calls under ~400 MiB little room on Windows)
  plasma flow         -> tokens written and sent on to pi
  coolant             -> GPU temperature

Layout: header (title, condition), the sidebar of departments (F1-F10), the warp core on the left of every page, and the
department's page on the right. Drawing: the frame on resize or page change ("static"), readouts once a second
("dyn"), and the core's colours 20 times a second without recreating anything.

Controls (CORE CONTROL, DIAGNOSTICS) act only when clicked; a control that changes Strata takes two clicks.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import math
import os
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import strata_control as ctl
from lcars_theme import (BG, BLUE, CORE_AMBER, CORE_DEAD, CORE_DIM, CORE_LIT, COIL_CACHE, COIL_MISS, COIL_PCIE, GOLD,
                         GREEN, GRID, INK, LILAC, MUTED, ORANGE, PEACH, REALIGN_GOLD, RED, RED_HI, SKY, TAN,
                         TRIAL_GREEN, VIOLET, coil_mix, mix)

CORE_PARTS = ("MATTER INJECTOR", "WARP COILS", "DILITHIUM CHAMBER", "CONTAINMENT FIELD", "COOLANT", "PLASMA FLOW",
              "ANTIMATTER INJECTOR")


def dept_of(ev: dict) -> int:
    """The department (page index) an event belongs to."""
    src, kind = ev.get("source"), ev.get("kind")
    if src == "control":
        return 2
    if src == "windows" or kind in ("gpu hot", "vram low", "ram full"):
        return 3
    if src == "pi" or kind in ("conversation shortened", "app hung up"):
        return 4
    if src == "config" or kind == "settings mismatch":
        return 6
    if src == "monitor":
        return 9
    return 1


class LcarsWindow:
    def __init__(self, an, sl, classic_cb=None, start_page: int = 0, title: str = "Strata Engineering"):
        from pages import PAGES
        self.an, self.sl = an, sl
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.title = title
        self.root.title(self.title)
        self.root.geometry("1560x900")
        self.root.minsize(1280, 780)
        self.root.configure(bg=BG)
        fams = set(tkfont.families(self.root))
        cond = "Bahnschrift SemiBold Condensed" if "Bahnschrift SemiBold Condensed" in fams else "Segoe UI Semibold"
        light = "Bahnschrift Condensed" if "Bahnschrift Condensed" in fams else "Segoe UI"
        self.f_title = (cond, 34)
        self.f_head = (cond, 15)
        self.f_lab = (cond, 12)
        self.f_small = (light, 11)
        self.f_tiny = (light, 10)
        self.f_big = (cond, 22)
        self.f_huge = (cond, 30)
        self._fonts: dict = {}
        self.c = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.W = self.H = 0
        self.L: dict = {}
        self.rings: list = []
        self.ring_cols: dict = {}
        self.ring_mix = None
        self.chamber = []
        self.field = []
        self.alertable: list = []
        self.phase = 0.0
        self.last_anim = time.time()
        self.top = False
        self.seen_events = None
        self.alert_until = 0.0
        self.alert_kind = ""
        self.status = ""
        self.snap: dict = {}
        self.handlers: dict = {}
        self.armed = None                       # (tag, time) of a two-click control waiting for its second click
        self.drag = None                        # the slider being dragged
        self.scan = None                        # diagnostics: {"start", "dur", "marks": {part: level}}
        self.pages = [cls(self) for cls in PAGES]
        self.dept_seen = [time.time()] * len(self.pages)
        self.dept_alert = [False] * len(self.pages)
        self.page = max(0, min(len(self.pages) - 1, start_page))
        self.c.bind("<Configure>", self.on_resize)
        self.c.bind("<MouseWheel>", self.on_wheel)
        self.c.bind("<Button-1>", self.on_press)
        self.c.bind("<B1-Motion>", self.on_motion)
        self.c.bind("<ButtonRelease-1>", self.on_release)
        self.c.bind("<Motion>", self.on_hover)
        for i in range(len(self.pages)):
            self.root.bind(f"<F{i + 1}>", lambda e, n=i: self.goto(n))
        self.root.bind("<Escape>", lambda e: self.disarm())

    # ------------------------------------------------------------ shapes
    def rect(self, x0, y0, x1, y1, fill, tags=("static",), outline=""):
        return self.c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, tags=tags)

    def pill(self, x0, y0, x1, y1, fill, tags=("static",), left=True, right=True):
        r = (y1 - y0) / 2
        ids = [self.rect(x0 + (r if left else 0), y0, x1 - (r if right else 0), y1, fill, tags)]
        if left:
            ids.append(self.c.create_oval(x0, y0, x0 + 2 * r, y1, fill=fill, outline="", tags=tags))
        if right:
            ids.append(self.c.create_oval(x1 - 2 * r, y0, x1, y1, fill=fill, outline="", tags=tags))
        return ids

    def text(self, x, y, s, font, fill=INK, anchor="w", tags=("dyn",), width=None):
        kw = {"width": width} if width else {}
        return self.c.create_text(x, y, text=s, font=font, fill=fill, anchor=anchor, tags=tags, **kw)

    def font(self, font) -> tkfont.Font:
        if font not in self._fonts:
            self._fonts[font] = tkfont.Font(font=font)
        return self._fonts[font]

    def measure(self, s, font) -> int:
        return self.font(font).measure(s)

    def fit(self, s: str, font, room: float) -> str:
        f = self.font(font)
        if f.measure(s) <= room:
            return s
        lo, hi = 0, len(s)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if f.measure(s[:mid] + "…") <= room:
                lo = mid
            else:
                hi = mid - 1
        return s[:lo] + "…"

    def elbow_top(self, x0, y0, sw, x1, y1, bh, color, r=20, R=44):
        t = ("static",)
        ids = [self.rect(x0, y0, x0 + sw, y1 - R + 1, color, t),
               self.rect(x0 + R - 1, y1 - R, x0 + sw + r, y1, color, t),
               self.rect(x0 + R - 1, y1 - bh, x1, y1, color, t),
               self.c.create_arc(x0, y1 - 2 * R, x0 + 2 * R, y1, start=180, extent=90, fill=color, outline="",
                                 style="pieslice", tags=t)]
        self.c.create_oval(x0 + sw, y1 - bh - 2 * r, x0 + sw + 2 * r, y1 - bh, fill=BG, outline="", tags=t)
        return ids

    def header(self, x0, y, x1, label, color, tags=("static",)):
        self.pill(x0, y, x0 + 26, y + 18, color, tags, right=False)
        self.text(x0 + 34, y + 9, label, self.f_head, color, tags=tags)
        w = self.measure(label, self.f_head)
        if x0 + 44 + w < x1 - 20:
            self.rect(x0 + 44 + w, y + 7, x1 - 9, y + 11, color, tags)
            self.c.create_oval(x1 - 18, y, x1, y + 18, fill=color, outline="", tags=tags)

    # ------------------------------------------------------------ widgets for the pages (all redrawn each second)
    def on(self, tag: str, fn) -> tuple:
        self.handlers[tag] = fn
        return ("dyn", tag, "click")

    def button(self, x0, y0, x1, y1, label, color, fn, key=None, confirm=None, enabled=True, active=False,
               sub=None, font=None):
        """A console button. With `confirm`, the first click arms it (it turns red and says what it will do) and a
        second click within 8 s runs it. A disabled button is dim; `sub` says why or what it does."""
        tag = "b:" + (key or label)
        armed = bool(self.armed and self.armed[0] == tag and time.time() - self.armed[1] < 8)
        col = RED_HI if armed else (mix(BG, color, 0.35) if not enabled else (mix(color, "#ffffff", 0.45)
                                                                                 if active else color))

        def click():
            if not enabled:
                return
            if confirm and not armed:
                self.armed = (tag, time.time())
            else:
                self.armed = None
                fn()
            self.draw_dynamic()
        tags = self.on(tag, click)
        if (y1 - y0) <= 30:
            self.pill(x0, y0, x1, y1, col, tags)
        else:
            self.rect(x0, y0, x1, y1, col, tags)
        f = font or self.f_lab
        txt = ("CONFIRM ▸ " + confirm) if armed else label
        if sub and (y1 - y0) > 30:
            self.text(x1 - 8, y0 + 10, self.fit(sub, self.f_tiny, x1 - x0 - 12), self.f_tiny, BG, anchor="e",
                      tags=tags)
            self.text(x1 - 8, y1 - 11, self.fit(txt, f, x1 - x0 - 12), f, BG, anchor="e", tags=tags)
        else:
            self.text((x0 + x1) / 2, (y0 + y1) / 2, self.fit(txt, f, x1 - x0 - 14), f, BG, anchor="center", tags=tags)
        return tag

    def slider(self, key, x0, y, x1, label, lo, hi, step, value, color, on_set, marker=None, fmt="{:g}",
               note=None, enabled=True):
        """A segmented slider (drag or click); `on_set(value)` runs on release. `marker` shows a reference value."""
        tag = "s:" + key
        if self.drag and self.drag["tag"] == tag:
            value = self.drag["value"]
        self.handlers[tag] = {"lo": lo, "hi": hi, "step": step, "x0": x0, "x1": x1, "on_set": on_set,
                              "enabled": enabled, "value": value}
        tags = ("dyn", tag, "click")
        shown = "DEFAULT" if value is None else fmt.format(value)
        self.text(x0, y + 7, label, self.f_lab, color if enabled else MUTED)
        self.text(x1, y + 7, shown, self.f_lab, INK if value is not None else MUTED, anchor="e")
        ty0, ty1 = y + 18, y + 31
        nseg = max(10, int((x1 - x0) / 9))
        segw = (x1 - x0) / nseg
        frac = 0 if value is None else (value - lo) / (hi - lo) if hi > lo else 0
        lit = round(max(0.0, min(1.0, frac)) * nseg)
        self.rect(x0, ty0 - 4, x1, ty1 + 4, BG, tags)                         # the hit area
        for k in range(nseg):
            col = (color if enabled else MUTED) if k < lit else GRID
            self.c.create_rectangle(x0 + k * segw + 1, ty0, x0 + (k + 1) * segw - 2, ty1, fill=col, outline="",
                                    tags=tags)
        if value is not None:
            kx = x0 + (x1 - x0) * max(0.0, min(1.0, frac))
            self.c.create_rectangle(kx - 3, ty0 - 4, kx + 3, ty1 + 4, fill="#ffffff", outline="", tags=tags)
        if marker is not None and hi > lo:
            mx = x0 + (x1 - x0) * max(0.0, min(1.0, (marker - lo) / (hi - lo)))
            self.c.create_polygon(mx - 5, ty1 + 10, mx + 5, ty1 + 10, mx, ty1 + 4, fill=TAN, outline="", tags=tags)
        if note:                                    # beside the label, so a stack of sliders stays compact
            lw = self.measure(label, self.f_lab) + 10
            room = x1 - x0 - lw - self.measure(shown, self.f_lab) - 14
            if room > 30:
                self.text(x0 + lw, y + 8, self.fit("· " + note, self.f_tiny, room), self.f_tiny, MUTED)

    def segbar(self, x0, y0, x1, y1, frac, color, nseg=None, hot=None):
        nseg = nseg or max(8, int((x1 - x0) / 10))
        segw = (x1 - x0) / nseg
        lit = round(max(0.0, min(1.0, frac or 0)) * nseg)
        for k in range(nseg):
            pos = (k + 1) / nseg
            col = GRID
            if k < lit:
                col = (RED_HI if hot and pos > hot[1] else GOLD if hot and pos > hot[0] else color)
            self.c.create_rectangle(x0 + k * segw + 1, y0, x0 + (k + 1) * segw - 2, y1, fill=col, outline="",
                                    tags=("dyn",))

    def chart(self, x0, y0, x1, y1, series, vmax=None, title=None, right=None, xs=None, hlines=(), fill=True,
              marks=(), title_col=None):
        """A small line chart: series = [(values, colour)], optional x positions (0..1) per series, horizontal
        reference lines [(value, colour, label)] and vertical marks [(x 0..1, colour)]."""
        c = self.c
        c.create_rectangle(x0, y0, x1, y1, fill="#07070c", outline=GRID, tags=("dyn",))
        c.create_rectangle(x0, y0, x0 + 5, y1, fill=title_col or (series[0][1] if series else MUTED), outline="",
                           tags=("dyn",))
        for k in range(1, 4):
            yy = y0 + (y1 - y0) * k / 4
            c.create_line(x0 + 6, yy, x1 - 1, yy, fill=GRID, dash=(2, 4), tags=("dyn",))
        if title:
            self.text(x0 + 12, y0 + 10, title, self.f_tiny, title_col or (series[0][1] if series else INK))
        if right:
            self.text(x1 - 6, y0 + 10, right, self.f_tiny, INK, anchor="e")
        allv = [v for vals, _ in series for v in vals if v is not None]
        top = vmax or max([10.0] + [v * 1.15 for v in allv] + [h[0] * 1.1 for h in hlines])
        px0, px1, py0, py1 = x0 + 9, x1 - 4, y0 + 22, y1 - 3
        for v, col, lab in hlines:
            yy = py1 - (py1 - py0) * min(1.0, v / top)
            c.create_line(x0 + 6, yy, x1 - 1, yy, fill=col, dash=(5, 3), width=1, tags=("dyn",))
            if lab:
                self.text(x1 - 6, yy - 7, lab, self.f_tiny, col, anchor="e")
        for xm, col in marks:
            xx = px0 + (px1 - px0) * xm
            c.create_line(xx, py0, xx, py1, fill=col, dash=(2, 3), tags=("dyn",))
        for si, (vals, col) in enumerate(series):
            n = len(vals)
            if n < 2:
                continue
            xpos = xs[si] if xs else [i / (n - 1) for i in range(n)]
            pts = []
            for xp, v in zip(xpos, vals):
                if v is None:
                    continue
                pts += [px0 + (px1 - px0) * xp, py1 - (py1 - py0) * min(1.0, v / top)]
            if len(pts) >= 4:
                if fill and si == 0:
                    c.create_polygon(pts[0], py1, *pts, pts[-2], py1, fill=mix(BG, col, 0.2), outline="",
                                     tags=("dyn",))
                c.create_line(*pts, fill=col, width=2, tags=("dyn",))
                c.create_oval(pts[-2] - 3, pts[-1] - 3, pts[-2] + 3, pts[-1] + 3, fill=col, outline="", tags=("dyn",))
        return top

    def gauge(self, cx, cy, r, frac, color, label, value, hot=False):
        """A round gauge: a 270° segmented arc, the value in the middle."""
        c = self.c
        n = 27
        lit = round(max(0.0, min(1.0, frac or 0)) * n)
        for k in range(n):
            a0 = 225 - k * 10
            col = (RED_HI if hot else color) if k < lit else GRID
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=a0 - 8, extent=8, style="arc", width=9, outline=col,
                         tags=("dyn",))
        self.text(cx, cy - 2, value, self.f_big, RED_HI if hot else INK, anchor="center")
        self.text(cx, cy + r - 4, label, self.f_tiny, color, anchor="center")

    def table(self, x0, y0, cols, rows, row_h=17, colors=None, font=None):
        """cols = [(title, x offset, anchor)]; rows = lists of strings."""
        for title, dx, anc in cols:
            self.text(x0 + dx, y0, title, self.f_tiny, MUTED, anchor=anc)
        for i, row in enumerate(rows):
            y = y0 + (i + 1) * row_h
            for (title, dx, anc), cell in zip(cols, row):
                self.text(x0 + dx, y, str(cell), font or self.f_small, (colors[i] if colors else INK), anchor=anc)

    def tiles(self, x0, y0, x1, items, h=58):
        """Big readouts: [(value, label, colour, bad)] — the number on top, a coloured label block under it."""
        n = len(items)
        w = (x1 - x0 - (n - 1) * 6) / n
        for i, (val, lab, col, bad) in enumerate(items):
            tx = x0 + i * (w + 6)
            self.text(tx + w - 4, y0 + 18, val, self.f_big, RED_HI if bad else col, anchor="e")
            self.c.create_rectangle(tx, y0 + 40, tx + w, y0 + h - 2, fill=RED_HI if bad else col, outline="",
                                    tags=("dyn",))
            self.text(tx + w - 4, y0 + 48, self.fit(lab, self.f_tiny, w - 8), self.f_tiny, BG, anchor="e")

    # ------------------------------------------------------------ layout and the static frame
    def on_resize(self, e) -> None:
        if (e.width, e.height) == (self.W, self.H):
            return
        self.W, self.H = e.width, e.height
        self.build_static()
        self.draw_dynamic()

    def goto(self, n: int) -> None:
        if n == self.page:
            return
        self.page = n
        self.dept_seen[n] = time.time()
        self.dept_alert[n] = False
        self.armed = None
        self.build_static()
        self.draw_dynamic()

    def build_static(self) -> None:
        c, W, H = self.c, self.W, self.H
        if not W:
            return
        c.delete("all")
        self.alertable = []
        SW, BH = 150, 22
        self.alertable += self.elbow_top(12, 12, SW, W - 220, 104, BH, ORANGE)
        x = W - 212
        for w, col in ((70, LILAC), (60, PEACH), (64, BLUE)):
            self.alertable += [self.rect(x, 104 - BH, x + w, 104, col)]
            x += w + 6
        self.alertable += self.pill(x, 104 - BH, W - 12, 104, LILAC, left=False)
        self.text(W - 16, 32, "STRATA ENGINEERING", self.f_title, ORANGE, anchor="e", tags=("static",))
        # departments
        y = 112
        n = len(self.pages)
        avail = H - 12 - 34 - 8 - y
        bh = max(44, min(58, int((avail - (n - 1) * 5) / n)))
        self.L["side"] = []
        for i in range(n):
            self.L["side"].append((12, y, 12 + SW, y + bh))
            y += bh + 5
        filler = H - 12 - y
        if filler > 40:
            self.alertable += [self.rect(12, y, 12 + SW, y + filler - 40, ORANGE)]
            self.alertable += self.pill(12, H - 12 - 34, 12 + SW, H - 12, ORANGE)
        # the core column, then the page
        x0 = 12 + SW + 18
        colA = (x0, 118, x0 + 440, H - 12)
        self.L["A"] = colA
        self.L["P"] = (colA[2] + 16, 118, W - 14, H - 12)
        self.header(colA[0], colA[1], colA[2], "WARP CORE · STRATA ENGINE", ORANGE)
        self.build_core()
        pg = self.pages[self.page]
        px0, py0, px1, py1 = self.L["P"]
        self.header(px0, py0, px1, f"{pg.code} · {pg.title}", pg.color)
        pg.build((px0, py0 + 32, px1, py1))
        self._fills = {i: c.itemcget(i, "fill") for i in self.alertable}
        self._alert_shown = None

    def build_core(self) -> None:
        c = self.c
        x0, y0, x1, y1 = self.L["A"]
        cx = (x0 + x1) / 2
        top, bot = y0 + 70, y1 - 120
        cy = (top + bot) / 2
        self.L["core"] = (cx, top, bot, cy)
        t = ("static",)
        self.orbit = c.create_oval(cx - 86, cy - 46, cx + 86, cy + 46, outline=GREEN, width=2, tags=t)
        self.field = [c.create_line(cx - 58, top + 10, cx - 58, bot - 10, width=3, dash=(6, 4), fill=GREEN, tags=t),
                      c.create_line(cx + 58, top + 10, cx + 58, bot - 10, width=3, dash=(6, 4), fill=GREEN, tags=t)]
        c.create_polygon(cx - 44, top - 26, cx + 44, top - 26, cx + 30, top, cx - 30, top, fill=ORANGE, outline="",
                         tags=t)
        c.create_polygon(cx - 30, bot, cx + 30, bot, cx + 44, bot + 26, cx - 44, bot + 26, fill=PEACH, outline="",
                         tags=t)
        self.inj_top = c.create_rectangle(cx - 22, top - 20, cx + 22, top - 6, fill=CORE_DIM, outline="", tags=t)
        self.inj_bot = c.create_rectangle(cx - 22, bot + 6, cx + 22, bot + 20, fill=CORE_DIM, outline="", tags=t)
        ch_h = 70
        self.chamber = [c.create_oval(cx - 56, cy - ch_h / 2, cx + 56, cy + ch_h / 2, fill=CORE_DIM, outline=SKY,
                                      width=2, tags=t),
                        c.create_oval(cx - 30, cy - 18, cx + 30, cy + 18, fill=CORE_DIM, outline="", tags=t)]
        self.rings = []
        gap, rh = 6, 13
        n = max(5, int((cy - ch_h / 2 - top - 8) // (rh + gap)))
        for i in range(n):
            yt = top + 6 + i * (rh + gap)
            yb = bot - 6 - i * (rh + gap)
            w = 36 + 6 * math.sin(math.pi * (i + 0.5) / n)
            self.rings.append((c.create_rectangle(cx - w, yt, cx + w, yt + rh, fill=CORE_DIM, outline="", tags=t), i, n))
            self.rings.append((c.create_rectangle(cx - w, yb - rh, cx + w, yb, fill=CORE_DIM, outline="", tags=t), i, n))
        self.ring_cols = {}
        self.ring_mix = None
        self.scan_bar = c.create_rectangle(cx - 72, top, cx + 72, top + 4, fill=TRIAL_GREEN, outline="",
                                           state="hidden", tags=t)
        lx, rx = x0 + 6, x1 - 6
        self.L["labels"] = {
            "MATTER INJECTOR": ("w", lx, top - 34, cx - 44, top - 14, ORANGE),
            "DILITHIUM CHAMBER": ("w", lx, cy - 30, cx - 56, cy, SKY),
            "ANTIMATTER INJECTOR": ("w", lx, bot + 4, cx - 44, bot + 14, PEACH),
            "WARP COILS": ("e", rx, top + 40, cx + 42, top + 50, BLUE),
            "CONTAINMENT FIELD": ("e", rx, cy - 30, cx + 58, cy - 10, GREEN),
            "COOLANT": ("e", rx, cy + 46, cx + 58, cy + 60, SKY),
            "PLASMA FLOW": ("e", rx, bot - 46, cx + 42, bot - 40, GOLD),
        }
        for name, (side, tx, ty, px, py, col) in self.L["labels"].items():
            w = self.measure(name, self.f_lab) + 10
            ex = tx + w if side == "w" else tx - w
            bx = px - 14 if side == "w" else px + 14
            c.create_line(ex, ty, bx, ty, bx, py, px, py, fill=col, width=2, tags=t)
            c.create_oval(ex - 4, ty - 4, ex + 4, ty + 4, fill=col, outline="", tags=t)
            c.create_oval(px - 3, py - 3, px + 3, py + 3, fill=col, outline="", tags=t)

    # ------------------------------------------------------------ the data layer (once a second)
    def draw_dynamic(self) -> None:
        if not self.W:
            return
        c = self.c
        c.delete("dyn")
        keep = self.drag["tag"] if self.drag else None
        self.handlers = {k: v for k, v in self.handlers.items() if k == keep}
        with self.an.lock:
            self.snap = s = dict(self.an.snap)
        self.draw_header(s)
        self.draw_sidebar()
        if not s:
            return
        eng, live, hw = s.get("engine") or {}, s.get("live") or {}, s.get("hardware") or {}
        self.draw_core_labels(s, eng, live, hw)
        try:
            self.pages[self.page].draw(s)
        except Exception:
            import traceback
            print(traceback.format_exc(), flush=True)
            px0, py0, px1, py1 = self.L["P"]
            self.text(px0, py0 + 50, "THIS PAGE HIT AN ERROR · SEE MONITOR.LOG", self.f_lab, RED_HI)

    def condition(self, s) -> tuple:
        now = time.time()
        with self.sl.lock:
            evs = [e for e in list(self.sl.events)[-200:] if e["ts"] > now - 600 and e.get("source") != "control"]
        if not s.get("up") or any(e["level"] == "error" for e in evs) or \
                any(lv == "bad" for lv, _ in s.get("checks") or []):
            return "RED", RED_HI
        if any(e["level"] == "warn" for e in evs):
            return "YELLOW", GOLD
        return "GREEN", GREEN

    def draw_header(self, s) -> None:
        W = self.W
        eng = s.get("engine") or {}
        now = dt.datetime.now()
        self.text(190, 28, now.strftime("%Y.%m.%d · %H:%M:%S"), self.f_head, TAN)
        model = eng.get("model") or "OFFLINE"
        self.text(190, 51, f"{model.upper()} · CONTEXT {eng.get('max_context') or 0:,} · ENGINE {eng.get('version', '?')}"
                  f" · KV {str(eng.get('kv', '?')).upper()}", self.f_small, LILAC)
        act = ctl.action.get("name")
        if act:
            msg = (f"▸ {act} in progress" + (f" · {ctl.action['note']}" if ctl.action.get("note") else "")
                   + f" · {time.time() - ctl.action['since']:.0f} s")
            col = GOLD
        elif ctl.last_result["text"] and time.time() - ctl.last_result["ts"] < 180:
            msg = f"▸ {dt.datetime.fromtimestamp(ctl.last_result['ts']):%H:%M:%S} {ctl.last_result['text']}"
            col = GREEN if ctl.last_result["ok"] else RED_HI
        elif self.status:
            msg, col = "▸ " + self.status, VIOLET
        else:
            msg, col = "", MUTED
        if msg:
            self.text(190, 70, self.fit(msg.upper(), self.f_tiny, W - 190 - 280), self.f_tiny, col)
        alert = time.time() < self.alert_until
        cond, ccol = self.condition(s) if s else ("RED", RED_HI)
        label = f"ALERT ▸ {self.alert_kind.upper()}" if alert else f"CONDITION {cond}"
        w = self.measure(label, self.f_lab) + 30
        self.pill(W - 16 - w, 61, W - 16, 77, RED_HI if alert else ccol, tags=("dyn",))
        self.text(W - 16 - w / 2, 69, label, self.f_lab, BG, anchor="center")

    def draw_sidebar(self) -> None:
        with self.sl.lock:
            evs = list(self.sl.events)[-300:]
        for ev in evs:
            if ev["level"] in ("warn", "error"):
                d = dept_of(ev)
                if ev["ts"] > self.dept_seen[d] and d != self.page:
                    self.dept_alert[d] = True
        blink = int(time.time() * 2) % 2 == 0
        cols = (ORANGE, PEACH, RED_HI, SKY, BLUE, GOLD, GREEN, LILAC, TAN, VIOLET)
        for i, pg in enumerate(self.pages):
            x0, y0, x1, y1 = self.L["side"][i]
            active = i == self.page
            col = cols[i % len(cols)]
            col = mix(col, "#ffffff", 0.5) if active else col
            tags = self.on(f"dept:{i}", lambda n=i: self.goto(n))
            self.rect(x0, y0, x1, y1, col, tags)
            if active:
                self.rect(x1 - 10, y0, x1, y1, BG, tags)
            self.text(x0 + 8, y0 + 9, f"{pg.code} · F{i + 1}", self.f_tiny, BG, tags=tags)
            tall = y1 - y0 >= 50
            self.text(x1 - (16 if active else 8), y1 - (22 if tall else 12), pg.title, self.f_lab, BG, anchor="e",
                      tags=tags)
            if tall:
                self.text(x1 - (16 if active else 8), y1 - 8, pg.sub, self.f_tiny, mix(BG, col, 0.3), anchor="e",
                          tags=tags)
            if self.dept_alert[i] and blink:
                self.c.create_oval(x0 + 8, y1 - 20, x0 + 20, y1 - 8, fill=RED_HI, outline=BG, width=2, tags=tags)

    def draw_core_labels(self, s, eng, live, hw) -> None:
        last = s.get("last_request") or {}
        mib = 1048576
        free = ((hw.get("gpu_mem_total") or 0) - (hw.get("gpu_mem_used") or 0)) / mib if hw.get("gpu_mem_total") else None
        state = live.get("state") or ("offline" if not s.get("up") else "idle")
        prefill = live.get("prefill_tok_s_mean")
        reading = (f"{live.get('prompt_read') or 0:,} / {live.get('prompt_total') or 0:,}"
                   if state == "reading" and live.get("prompt_total") else
                   f"LAST {last.get('prompt_read', 0):,} READ" if last else "—")
        temp = hw.get("gpu_temp")
        mx = coil_mix(last) if last else None
        vals = {
            "MATTER INJECTOR": ("PROMPT INTAKE", f"{reading}" + (f" · {prefill:.0f} TOK/S" if prefill else "")),
            "DILITHIUM CHAMBER": ("GPU", f"LOAD {hw.get('gpu_util', '—')}% · {hw.get('gpu_power') or 0:.0f} W"),
            "ANTIMATTER INJECTOR": ("EXPERT STREAM · PCIe",
                                    f"{hw.get('gpu_pcie_rx_mb') or 0:,.0f} MB/S"
                                    + (f" · {last.get('pcie_share', 0):.0%} STREAMED" if last else "")),
            "WARP COILS": ("EXPERTS: CACHE · RAM · PCIe",
                           f"{mx[0]:.0%} · {mx[1]:.0%} · {mx[2]:.0%}" if mx else f"{eng.get('expert_slots', 0):,} RESIDENT"),
            "CONTAINMENT FIELD": ("FREE VRAM", f"{free:,.0f} MiB" if free is not None else "—"),
            "COOLANT": ("GPU TEMPERATURE", f"{temp} °C" if temp is not None else "—"),
            "PLASMA FLOW": ("OUTPUT TO PI", f"{live.get('tok_s') or 0:.1f} TOK/S" if state == "generating"
                            else f"LAST {last.get('decode_tok_s', 0):.1f} TOK/S" if last else "—"),
        }
        marks = (self.scan or {}).get("marks") or {}
        for name, (side, tx, ty, px, py, col) in self.L["labels"].items():
            sub, val = vals[name]
            anchor = "w" if side == "w" else "e"
            warn = (name == "CONTAINMENT FIELD" and free is not None and free < self.sl.VRAM_LOW_MIB) or \
                   (name == "COOLANT" and temp is not None and temp >= self.sl.GPU_HOT_C)
            self.text(tx, ty, name, self.f_lab, RED_HI if warn else col, anchor)
            self.text(tx, ty + 17, sub, self.f_tiny, MUTED, anchor)
            self.text(tx, ty + 33, val, self.f_small, RED_HI if warn else INK, anchor)
            if name in marks:
                sym, mc = {"ok": ("✓", GREEN), "warn": ("!", GOLD), "bad": ("✕", RED_HI)}[marks[name]]
                w = self.measure(name, self.f_lab)
                mx_ = tx + w + 26 if side == "w" else tx - w - 26
                self.c.create_oval(mx_ - 9, ty - 9, mx_ + 9, ty + 9, fill=mc, outline="", tags=("dyn",))
                self.text(mx_, ty, sym, self.f_lab, BG, anchor="center")
        fcol = GREEN if free is None or free >= 400 else GOLD if free >= self.sl.VRAM_LOW_MIB else RED_HI
        for f in self.field:
            self.c.itemconfigure(f, fill=fcol)
        self.c.itemconfigure(self.orbit, outline=fcol)
        # the coils' colours: the last request's mix, spread evenly over the rings
        if mx != self.ring_mix:
            self.ring_mix = mx
            self.ring_cols = {}
            if mx:
                acc = [0.0, 0.0, 0.0]
                for item, i, n in sorted(self.rings, key=lambda r: (r[1], r[0])):
                    for k in range(3):
                        acc[k] += mx[k]
                    k = max(range(3), key=lambda j: acc[j])
                    acc[k] -= 1.0
                    self.ring_cols[item] = (COIL_CACHE, COIL_MISS, COIL_PCIE)[k]
        x0, y0, x1, y1 = self.L["A"]
        st = self.core_state(s)
        lab, colr = {"generating": ("GENERATING", CORE_LIT), "reading": ("READING PROMPT", CORE_AMBER),
                     "realign": ("FULL RE-READ", REALIGN_GOLD), "idle": ("STANDING BY", TAN),
                     "queued": ("QUEUED", GOLD), "trial": ("SPEED TRIAL", TRIAL_GREEN),
                     "standby": ("ENGINE ON STANDBY", MUTED), "loading": ("POWERING UP", GOLD),
                     "stopping": ("POWERING DOWN", GOLD), "scan": ("DIAGNOSTIC SCAN", TRIAL_GREEN)
                     }.get(st, ("OFFLINE", RED_HI))
        self.pill(x0, y1 - 76, x0 + 120, y1 - 52, colr, tags=("dyn",), right=False)
        self.text(x0 + 130, y1 - 64, f"CORE STATUS ▸ {lab}", self.f_head, colr)
        phase = str(live.get("phase") or "")
        extra = []
        if live.get("elapsed_s"):
            extra.append(f"{live['elapsed_s']:.0f} S")
        if live.get("generated"):
            extra.append(f"{live['generated']:,} TOKENS WRITTEN")
        if live.get("queued"):
            extra.append(f"{live['queued']} WAITING")
        self.text(x0, y1 - 36, (phase.upper() + ("  ·  " if phase and extra else "") + "  ·  ".join(extra)) or
                  "NO ACTIVE REQUEST", self.f_small, MUTED, width=x1 - x0)
        if st in ("standby", "offline"):
            cx, top, bot, cy = self.L["core"]
            self.pill(cx - 62, cy - 12, cx + 62, cy + 12, MUTED if st == "standby" else RED, tags=("dyn",))
            self.text(cx, cy, "STANDBY" if st == "standby" else "OFFLINE", self.f_lab, BG, anchor="center")

    def core_state(self, s) -> str:
        act = (ctl.action.get("name") or "").lower()
        live = s.get("live") or {}
        if "trial" in act or "RUN" in (ctl.action.get("note") or ""):
            return "trial"
        if act in ("engage", "start strata") or (act in ("restart engine", "full restart") and
                                                 live.get("state") in (None, "unloaded")):
            return "loading"
        if act in ("standby", "shut down"):
            return "stopping"
        if self.scan and time.time() - self.scan["start"] < self.scan["dur"]:
            return "scan"
        if not s.get("up"):
            return "offline"
        st = live.get("state") or "idle"
        if st == "unloaded":
            return "standby"
        if st == "reading" and (live.get("prompt_total") or 0) > 30000 and (live.get("elapsed_s") or 0) > 6:
            return "realign"
        if live.get("queued") and st == "idle":
            return "queued"
        return st if st in ("generating", "reading", "idle") else "idle"

    # ------------------------------------------------------------ animation (20 per second)
    def animate(self) -> None:
        try:
            now = time.time()
            dtm = now - self.last_anim
            self.last_anim = now
            s = self.snap or {}
            live, hw = s.get("live") or {}, s.get("hardware") or {}
            st = self.core_state(s) if s else "offline"
            free = ((hw.get("gpu_mem_total") or 0) - (hw.get("gpu_mem_used") or 0)) / 1048576 \
                if hw.get("gpu_mem_total") else None
            strained = (free is not None and free < 400) or (hw.get("gpu_temp") or 0) >= self.sl.GPU_HOT_C
            alert = now < self.alert_until
            override = None
            if st == "generating":
                speed = 2.0 + (live.get("tok_s") or 0) / 5.0
            elif st == "reading":
                speed, override = 2.0 + (live.get("prefill_tok_s_mean") or 600) / 120.0, CORE_AMBER
            elif st == "realign":
                speed, override = 3.0, REALIGN_GOLD
            elif st in ("trial", "scan"):
                speed, override = 3.0, TRIAL_GREEN
            elif st == "offline":
                speed, override = 0.0, CORE_DEAD
            elif st == "standby":
                speed, override = 0.0, "#0c1524"
            else:
                speed = 0.7
            if alert:
                override = RED_HI
            self.phase += speed * dtm
            prog = (now * 0.6) % 1.0
            for item, i, n in self.rings:
                if st in ("loading", "stopping"):
                    lit = (i / n < prog) if st == "loading" else (i / n >= prog)
                    col = mix(CORE_DIM, GOLD, 0.85 if lit else 0.05)
                elif speed == 0:
                    col = override or CORE_DEAD
                else:
                    b = 0.0
                    for j in range(3):                      # three pulses in flight, from each injector inwards
                        pos = (self.phase + j * n / 3) % n
                        b = max(b, math.exp(-((i - pos) ** 2) / 0.9))
                    if alert:
                        b = 0.5 + 0.5 * math.sin(now * 8)
                    target = override or self.ring_cols.get(item, CORE_LIT)
                    if strained and not override:
                        target = mix(target, ORANGE, 0.55)
                    if st == "idle" and not alert:
                        level = 0.12 + 0.5 * b              # idle: the coils show their mix, dimly, slowly
                    else:
                        level = 0.18 + 0.82 * b
                    col = mix(CORE_DIM, target, level)
                self.c.itemconfigure(item, fill=col)
            util = (hw.get("gpu_util") or 0) / 100 if speed else 0
            glow = 0.25 + 0.75 * util * (0.8 + 0.2 * math.sin(now * 6))
            if self.chamber:
                self.c.itemconfigure(self.chamber[0], fill=mix(CORE_DIM, RED_HI if alert else SKY, glow * 0.6))
                self.c.itemconfigure(self.chamber[1], fill=mix(CORE_DIM, "#ffffff", glow))
            pulse = 0.5 + 0.5 * math.sin(now * 8)
            if hasattr(self, "inj_top"):
                self.c.itemconfigure(self.inj_top, fill=mix(CORE_DIM, CORE_AMBER, pulse if st in ("reading", "realign")
                                                            else 0.15))
                self.c.itemconfigure(self.inj_bot, fill=mix(CORE_DIM, PEACH,
                                                            min(1.0, (hw.get("gpu_pcie_rx_mb") or 0) / 1500) * pulse))
            if strained and self.field and int(now * 6) % 3 == 0:     # the containment field flickers under strain
                for f in self.field:
                    self.c.itemconfigure(f, fill=GOLD)
            if self.scan and now - self.scan["start"] < self.scan["dur"] and self.L.get("core"):
                cx, top, bot, cy = self.L["core"]
                fr = ((now - self.scan["start"]) / 1.6) % 1.0
                yy = top - 26 + (bot + 52 - top) * fr
                self.c.coords(self.scan_bar, cx - 72, yy, cx + 72, yy + 4)
                self.c.itemconfigure(self.scan_bar, state="normal")
            elif hasattr(self, "scan_bar"):
                self.c.itemconfigure(self.scan_bar, state="hidden")
            on = alert and int(now * 2) % 2 == 0
            if on != getattr(self, "_alert_shown", None):
                self._alert_shown = on
                for item in self.alertable:
                    self.c.itemconfigure(item, fill=RED if on else self._fills.get(item, ORANGE))
            self.root.title(("ALERT ▸ " if alert else "") + self.title)
        except Exception:
            import traceback
            print(traceback.format_exc(), flush=True)
        self.root.after(50, self.animate)

    # ------------------------------------------------------------ input
    def _target(self, e):
        for item in reversed(self.c.find_overlapping(e.x, e.y, e.x, e.y)):
            for t in self.c.gettags(item):
                if t in self.handlers:
                    return t
        return None

    def on_press(self, e) -> None:
        t = self._target(e)
        if t is None:
            if self.armed:
                self.disarm()
            return
        h = self.handlers[t]
        if t.startswith("s:"):
            if not h["enabled"]:
                return
            self.drag = {"tag": t, "value": self.slider_value(h, e.x)}
            self.draw_dynamic()
            return
        if self.armed and self.armed[0] != t:
            self.armed = None
        h()

    @staticmethod
    def slider_value(h, x):
        fr = max(0.0, min(1.0, (x - h["x0"]) / max(1, h["x1"] - h["x0"])))
        v = h["lo"] + fr * (h["hi"] - h["lo"])
        v = round(round(v / h["step"]) * h["step"], 6)
        return int(v) if float(h["step"]).is_integer() else v

    def on_motion(self, e) -> None:
        if self.drag:
            h = self.handlers.get(self.drag["tag"])
            if h:
                self.drag["value"] = self.slider_value(h, e.x)
                self.draw_dynamic()

    def on_release(self, e) -> None:
        if self.drag:
            d, self.drag = self.drag, None
            h = self.handlers.get(d["tag"])
            if h and d["value"] != h.get("value"):
                h["on_set"](d["value"])
            self.draw_dynamic()

    def on_hover(self, e) -> None:
        self.c.configure(cursor="hand2" if self._target(e) else "")

    def disarm(self) -> None:
        if self.armed:
            self.armed = None
            self.draw_dynamic()

    def on_wheel(self, e) -> None:
        if self.pages[self.page].on_wheel(e):
            self.draw_dynamic()

    def flash(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    def check_alerts(self) -> None:
        """A new warning or error (not one from the panel's own controls): red alert for 30 s."""
        with self.sl.lock:
            evs = list(self.sl.events)[-400:]
        key = (len(evs), evs[-1]["ts"] if evs else 0)
        if key != self.seen_events:
            new_bad = [e for e in evs if self.seen_events is not None and e["ts"] > (self.seen_events[1] or 0)
                       and e["level"] in ("warn", "error") and e.get("source") != "control"]
            self.seen_events = key
            if new_bad:
                self.alert_until = time.time() + 30
                self.alert_kind = new_bad[-1]["kind"]
                self.flash()

    def make_bundle(self) -> None:
        """A diagnostics bundle, saved quietly (nothing opens)."""
        self.status = "making a diagnostics bundle …"

        def work():
            try:
                p = self.sl.make_bundle()
                self.status = "diagnostics bundle saved · " + os.path.basename(p)
            except Exception:
                self.status = "the diagnostics bundle failed · see monitor.log"
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ run
    def refresh(self) -> None:
        try:
            self.check_alerts()
            if not self.drag:
                self.draw_dynamic()
        except Exception:
            import traceback
            print(traceback.format_exc(), flush=True)
        self.root.after(1000, self.refresh)

    def start(self) -> None:
        self.root.after(300, self.refresh)
        self.root.after(400, self.animate)
        self.root.mainloop()

    def text_dump(self) -> str:
        out = []
        for item in self.c.find_withtag("dyn"):
            if self.c.type(item) == "text":
                out.append(self.c.itemcget(item, "text"))
        return "\n".join(out)
