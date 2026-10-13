"""09 REPORTS: a report for the captain, drawn here and saved quietly as HTML (with charts) and Markdown."""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

import report_maker as rm
from lcars_theme import BG, BLUE, GOLD, GREEN, GRID, INK, KIND_COLORS, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN
from pages.base import Page


class ReportsPage(Page):
    code, title, sub, color = "09", "REPORTS", "for the captain", GOLD

    def __init__(self, w):
        super().__init__(w)
        self.rep = None
        self.path = None
        self.busy = False
        self.files: list = []
        self.listed = 0.0

    def make(self, name: str) -> None:
        if self.busy:
            return
        self.busy = True
        self.w.status = f"writing the {name} report …"

        def work():
            try:
                rep = rm.build(self.w.an, name)
                html_path, md_path = rm.write(rep)
                self.rep, self.path = rep, html_path
                self.w.status = f"report saved quietly · {os.path.basename(html_path)} (+ .md)"
                self.listed = 0
            except Exception as e:
                self.w.status = f"the report failed: {e}"
            finally:
                self.busy = False
        threading.Thread(target=work, daemon=True).start()

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                self.rep = json.load(f)
            self.path = path[:-5] + ".html"
        except (OSError, ValueError):
            pass

    def draw(self, s) -> None:
        w = self.w
        x0, y0, x1, y1 = self.area
        if time.time() - self.listed > 10:
            self.files, self.listed = rm.listing(30), time.time()
        names = [("hour", "LAST HOUR"), ("shift", "THIS SHIFT · 8 H"), ("today", "TODAY"), ("start", "SINCE START")]
        bw = (x1 - x0 - 3 * 8) / 4
        for i, (key, lab) in enumerate(names):
            bx = x0 + i * (bw + 8)
            w.button(bx, y0, bx + bw, y0 + 40, lab, GOLD, lambda k=key: self.make(k), key="rep:" + key,
                     enabled=not self.busy, sub="write a report")
        L, R = self.cols(2, area=(x0, y0 + 52, x1, y1))
        rep = self.rep
        lx0, ly0, lx1, ly1 = L
        if not rep:
            self.sub_header(lx0, ly0, lx1, "NO REPORT OPEN", MUTED)
            w.text(lx0, ly0 + 34, "PICK A PERIOD ABOVE. THE REPORT SHOWS HERE AND IS SAVED TO LOGS\\REPORTS AS HTML "
                                  "AND MARKDOWN. NOTHING OPENS ON ITS OWN.", w.f_tiny, MUTED, width=lx1 - lx0)
        else:
            f = lambda t: dt.datetime.fromtimestamp(t).strftime("%m.%d %H:%M")
            self.sub_header(lx0, ly0, lx1, f"{rep['label'].upper()} · {f(rep['t0'])}–{f(rep['t1'])}", GOLD)
            w.text(lx0, ly0 + 32, rep["verdict"], w.f_small, INK, width=lx1 - lx0)
            w.tiles(lx0, ly0 + 56, lx1, [
                (f"{rep['requests']:,}", "REQUESTS", BLUE, False),
                (f"{rep['busy_min']:.0f}m", "BUSY", SKY, False),
                (f"{rep['tok_median']:.1f}" if rep.get("tok_median") else "—", "TOK/S MEDIAN", GOLD, False),
                (f"{rep['share']:.0%}", "ON COMPACTION", PEACH, rep["share"] > 0.25)])
            # busy split
            by = ly0 + 126
            tot = sum(rep["busy"].values()) or 1
            xx = lx0
            for k in ("coding turn", "summary", "failed summary", "full re-read"):
                dx = (lx1 - lx0) * rep["busy"][k] / tot
                if dx >= 2:
                    self.c.create_rectangle(xx, by, xx + dx - 2, by + 16, fill=KIND_COLORS[k], outline="",
                                            tags=("dyn",))
                xx += dx
            w.text(lx0, by + 28, "  ·  ".join(f"{k.upper()} {rep['busy'][k] / 60:.0f}M" for k in rep["busy"]),
                   w.f_tiny, INK)
            pts = rep.get("tok_series") or []
            if len(pts) >= 2:
                t0, t1 = pts[0][0], pts[-1][0]
                w.chart(lx0, by + 44, lx1, by + 150, [([v for _, v in pts], GOLD)],
                        xs=[[(t - t0) / max(1, t1 - t0) for t, _ in pts]], title="WRITING TOK/S PER REQUEST",
                        fill=False)
            yy = by + 166
            self.sub_header(lx0, yy, lx1, "WARNINGS", RED_HI)
            yy += 30
            items = sorted(rep["warnings"].items(), key=lambda kv: -kv[1]) or [("none", "")]
            for k, v in items[:max(1, int((ly1 - yy) // 16))]:
                w.text(lx0, yy, k.upper(), w.f_tiny, GOLD if v else MUTED)
                w.text(lx1, yy, str(v), w.f_tiny, INK, anchor="e")
                yy += 16
        # saved reports
        rx0, ry0, rx1, ry1 = R
        self.sub_header(rx0, ry0, rx1, "SAVED REPORTS", LILAC)
        y = ry0 + 32
        for p in self.files[:max(1, int((ry1 - ry0 - 90) // 20))]:
            name = os.path.basename(p)[7:-5]
            cur = self.path and self.path[:-5] == p[:-5]
            tags = w.on("repf:" + name, lambda p=p: self.load(p))
            w.rect(rx0, y - 9, rx1, y + 9, "#1a1622" if cur else BG, tags)
            w.text(rx0, y, name.upper(), w.f_small, INK if cur else LILAC, tags=tags)
            y += 20
        if not self.files:
            w.text(rx0, y, "NONE YET", w.f_tiny, MUTED)
        if self.path:
            w.button(rx0, ry1 - 30, rx1, ry1, "OPEN THIS REPORT IN THE BROWSER", BLUE,
                     lambda: os.startfile(self.path), key="openrep")
            w.text(rx0, ry1 - 42, w.fit(self.path, w.f_tiny, rx1 - rx0), w.f_tiny, MUTED)
