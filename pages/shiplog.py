"""08 SHIP'S LOG: every event, with filters by department and level, warnings per hour, and the incident snapshots."""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import time

from lcars_theme import BG, BLUE, GOLD, GREEN, GRID, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, VIOLET
from pages.base import Page

FILTERS = [("ALL", None), ("STRATA", {"strata"}), ("PI", {"pi"}), ("HARDWARE", {"windows", "hw"}),
           ("CONFIG", {"config"}), ("CONTROL", {"control"}), ("MONITOR", {"monitor"}), ("INCIDENTS", "inc")]
HW_KINDS = {"gpu hot", "vram low", "ram full"}


class ShipLogPage(Page):
    code, title, sub, color = "08", "SHIP'S LOG", "every event", LILAC

    def __init__(self, w):
        super().__init__(w)
        self.flt = 0
        self.warn_only = False
        self.hide_requests = True
        self.offset = 0
        self.selected = None
        self.rows_r = (0, 0, 0, 0)

    def match(self, e) -> bool:
        f = FILTERS[self.flt][1]
        if self.warn_only and e["level"] == "info":
            return False
        if self.hide_requests and e["kind"] == "request":
            return False
        if f is None:
            return True
        if "hw" in f and e["kind"] in HW_KINDS:
            return True
        return e.get("source") in f and not (e["kind"] in HW_KINDS and "hw" not in f)

    def incidents(self) -> list:
        if time.time() - getattr(self, "_inc_at", 0) < 15:
            return self._inc
        self._inc_at = time.time()
        self._inc = out = []
        for p in sorted(glob.glob(os.path.join(self.w.sl.LOG_DIR, "incidents", "*.json")))[-200:]:
            try:
                with open(p, encoding="utf-8") as f:
                    inc = json.load(f)
                t = inc.get("trigger") or {}
                out.append({"ts": inc.get("ts") or t.get("ts") or os.path.getmtime(p), "level": t.get("level", "warn"),
                            "source": "incident", "kind": t.get("kind", "incident"),
                            "text": f"{os.path.basename(p)} · {t.get('text', '')} · {len(inc.get('events') or [])} "
                                    f"event(s) saved with it, and the engine and hardware at the time"})
            except (OSError, ValueError):
                pass
        return out

    def draw(self, s) -> None:
        w = self.w
        x0, y0, x1, y1 = self.area
        # filters
        n = len(FILTERS)
        bw = (x1 - x0 - (n - 1) * 5) / n
        for i, (lab, _) in enumerate(FILTERS):
            bx = x0 + i * (bw + 5)

            def pick(i=i):
                self.flt, self.offset = i, 0
            w.button(bx, y0, bx + bw, y0 + 22, lab, LILAC, pick, key=f"flt{i}", active=i == self.flt,
                     font=w.f_tiny)
        y = y0 + 30

        def tog_w():
            self.warn_only, self.offset = not self.warn_only, 0

        def tog_r():
            self.hide_requests, self.offset = not self.hide_requests, 0
        w.button(x0, y, x0 + 170, y + 20, "WARNINGS ONLY" if self.warn_only else "ALL LEVELS", GOLD, tog_w,
                 key="warnonly", active=self.warn_only, font=w.f_tiny)
        w.button(x0 + 178, y, x0 + 348, y + 20, "REQUESTS HIDDEN" if self.hide_requests else "REQUESTS SHOWN", SKY,
                 tog_r, key="hidereq", active=not self.hide_requests, font=w.f_tiny)
        # warnings per hour, today
        with w.sl.lock:
            allev = list(w.sl.events)
        start = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        per = [0] * 24
        for e in allev:
            if e["ts"] >= start and e["level"] in ("warn", "error"):
                per[min(23, int((e["ts"] - start) // 3600))] += 1
        w.text(x0 + 360, y + 10, "WARNINGS / HOUR", w.f_tiny, MUTED)
        sx0 = x0 + 360 + w.measure("WARNINGS / HOUR", w.f_tiny) + 10
        top = max(per + [1])
        cw = (x1 - sx0) / 24
        for h, v in enumerate(per):
            hh = 18 * v / top
            self.c.create_rectangle(sx0 + h * cw + 1, y + 20 - max(1, hh), sx0 + (h + 1) * cw - 1, y + 20,
                                    fill=(RED_HI if v >= 10 else GOLD) if v else GRID, outline="", tags=("dyn",))
        y += 30
        # the rows
        evs = self.incidents() if FILTERS[self.flt][1] == "inc" else [e for e in allev if self.match(e)]
        evs = list(reversed(evs))
        dy0 = y1 - 70
        rows = max(1, int((dy0 - y - 6) // 18))
        self.rows_r = (x0, y, x1, dy0)
        self.offset = max(0, min(self.offset, max(0, len(evs) - rows)))
        for i, ev in enumerate(evs[self.offset:self.offset + rows]):
            yy = y + i * 18 + 8
            key = (ev["ts"], ev["kind"])
            col = {"error": RED_HI, "warn": GOLD}.get(ev["level"], MUTED)

            def pick(k=key):
                self.selected = None if self.selected == k else k
            tags = w.on(f"slog:{ev['ts']}:{ev['kind']}", pick)
            w.rect(x0, yy - 9, x1, yy + 9, "#1a1622" if self.selected == key else BG, tags)
            w.text(x0, yy, dt.datetime.fromtimestamp(ev["ts"]).strftime("%m.%d %H:%M:%S"), w.f_small, LILAC, tags=tags)
            w.text(x0 + 100, yy, w.fit(ev.get("source", "").upper(), w.f_tiny, 66), w.f_tiny, MUTED, tags=tags)
            w.text(x0 + 170, yy, w.fit(ev["kind"].upper(), w.f_small, 150), w.f_small, col, tags=tags)
            w.text(x0 + 326, yy, w.fit(ev["text"].replace("\n", " "), w.f_small, x1 - x0 - 326), w.f_small,
                   INK if ev["level"] != "info" else MUTED, tags=tags)
        if not evs:
            w.text(x0, y + 10, "NOTHING MATCHES THIS FILTER", w.f_tiny, MUTED)
        # detail
        self.c.create_rectangle(x0, dy0, x1, y1, fill="#0b0b10", outline=GRID, tags=("dyn",))
        w.text(x1 - 8, dy0 + 10, f"{self.offset + 1}–{min(len(evs), self.offset + rows)} OF {len(evs)} · SCROLL",
               w.f_tiny, MUTED, anchor="e")
        sel = next((e for e in evs if self.selected == (e["ts"], e["kind"])), None)
        w.text(x0 + 10, dy0 + 12, (f"{w.sl.local(sel['ts'])} · {sel.get('source', '').upper()} · "
                                   f"{sel['kind'].upper()}") if sel else "SELECT AN ENTRY TO READ ALL OF IT",
               w.f_tiny, LILAC if sel else MUTED)
        if sel:
            w.text(x0 + 10, dy0 + 28, sel["text"], w.f_tiny, INK, width=x1 - x0 - 20)

    def on_wheel(self, e) -> bool:
        x0, y0, x1, y1 = self.rows_r
        if x0 <= e.x <= x1 and y0 <= e.y <= y1:
            self.offset = max(0, self.offset - (4 if e.delta > 0 else -4))
            return True
        return False
