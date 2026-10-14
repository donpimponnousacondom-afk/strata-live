"""10 MAINTENANCE: upkeep — the engine's counters, the log folder, bundles, always on top, and the monitor's health."""
from __future__ import annotations

import datetime as dt
import os
import threading
import time

import strata_control as ctl
from lcars_theme import BG, BLUE, GOLD, GREEN, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, VIOLET, fmt_k
from pages.base import Page
from version import VERSION

STARTED = time.time()


class MaintPage(Page):
    code, title, sub, color = "10", "MAINTENANCE", "upkeep", VIOLET

    def __init__(self, w):
        super().__init__(w)
        self.folder = {}
        self.scanned = 0.0

    def scan(self) -> None:
        if time.time() - self.scanned < 30:
            return
        self.scanned = time.time()

        def work():
            size = files = 0
            oldest = None
            for root, _, names in os.walk(self.w.sl.LOG_DIR):
                for n in names:
                    p = os.path.join(root, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    size += st.st_size
                    files += 1
                    oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
            tb = 0
            try:
                with open(os.path.join(self.w.sl.LOG_DIR, "monitor.log"), encoding="utf-8", errors="replace") as f:
                    tb = f.read()[-300000:].count("Traceback")
            except OSError:
                pass
            backups = len(os.listdir(ctl.CONFIG_BACKUPS)) if os.path.isdir(ctl.CONFIG_BACKUPS) else 0
            self.folder = {"size": size, "files": files, "oldest": oldest, "tracebacks": tb, "config_backups": backups}
        threading.Thread(target=work, daemon=True).start()

    def toggle_top(self) -> None:
        self.w.top = not self.w.top
        self.w.root.attributes("-topmost", self.w.top)

    def draw(self, s) -> None:
        self.scan()
        w = self.w
        x0, y0, x1, y1 = self.area
        tot = s.get("totals") or {}
        since = tot.get("since")
        up = (time.time() - since) if since and s.get("up") else None
        prompt, reused = tot.get("prompt_tokens") or 0, tot.get("reused") or 0
        self.sub_header(x0, y0, x1, "ENGINE COUNTERS · SINCE STRATA STARTED", ORANGE)
        w.tiles(x0, y0 + 30, x1, [
            (f"{int(up // 3600)}h {int(up % 3600 // 60):02d}m" if up else "—", "UPTIME", PEACH, False),
            (f"{tot.get('requests', 0):,}", "REQUESTS", TAN, False),
            (fmt_k(prompt), "TOKENS READ", BLUE, False),
            (f"{reused / prompt:.1%}" if prompt else "—", "FROM THE CACHE", GREEN, False),
            (fmt_k(tot.get("output_tokens")), "TOKENS WRITTEN", LILAC, False),
            (f"{s.get('compactions_today', 0)}", "COMPACTIONS TODAY", GOLD, False)])
        pt, dtm = (tot.get("prompt_ms") or 0) / 1000, (tot.get("decode_ms") or 0) / 1000
        w.text(x0, y0 + 104, f"STRATA SPENT {pt / 60:.0f} MIN READING PROMPTS AND {dtm / 60:.0f} MIN WRITING · STARTED "
                             f"{dt.datetime.fromtimestamp(since):%m.%d %H:%M}" if since else "STRATA IS NOT ANSWERING",
               w.f_tiny, MUTED)
        L, R = self.cols(2, area=(x0, y0 + 124, x1, y1))
        lx0, ly0, lx1, ly1 = L
        self.sub_header(lx0, ly0, lx1, "TOOLS", VIOLET)
        y = ly0 + 30
        w.button(lx0, y, lx1, y + 40, "DIAGNOSTICS BUNDLE", VIOLET, w.make_bundle, key="bundle",
                 sub="a zip of logs and settings · saved quietly")
        y += 48
        w.button(lx0, y, lx1, y + 40, "ALWAYS ON TOP", GOLD, self.toggle_top, key="top", active=w.top,
                 sub="on" if w.top else "off")
        y += 48
        w.button(lx0, y, lx1, y + 40, "OPEN THE LOG FOLDER", SKY, lambda: os.startfile(w.sl.LOG_DIR), key="logs",
                 sub="opens explorer · leaves the panel")
        y += 56
        self.sub_header(lx0, y, lx1, "LOG FOLDER", BLUE)
        f = self.folder
        info = [("PLACE", w.sl.LOG_DIR), ("SIZE", f"{(f.get('size') or 0) / 1e6:.1f} MB in {f.get('files', 0):,} files"),
                ("OLDEST", dt.datetime.fromtimestamp(f["oldest"]).strftime("%Y-%m-%d") if f.get("oldest") else "—"),
                ("CONFIG COPIES", f"{f.get('config_backups', 0)} in backups\\config")]
        y += 30
        for k, v in info:
            w.text(lx0, y, k, w.f_tiny, TAN)
            w.text(lx0 + 100, y, w.fit(str(v), w.f_tiny, lx1 - lx0 - 100), w.f_tiny, INK)
            y += 16
        rx0, ry0, rx1, ry1 = R
        self.sub_header(rx0, ry0, rx1, "MONITOR HEALTH", GREEN)
        import threading as th
        names = sorted(t.name for t in th.enumerate())
        with w.sl.lock:
            nev = len(w.sl.events)
            nsamp = len(w.sl.samples)
        tb = f.get("tracebacks", 0)
        rows = [
            ("VERSION", f"CONSOLE {VERSION} · WATCHERS {w.sl.VERSION}"),
            ("RUNNING FOR", f"{(time.time() - STARTED) / 60:.0f} MIN"),
            ("THREADS", f"{len(names)} running"),
            ("EVENTS IN MEMORY", f"{nev:,} · {nsamp:,} SENSOR SAMPLES"),
            ("REQUESTS TRACKED", f"{len(w.an.reqs):,}"),
            ("ERRORS IN MONITOR.LOG", f"{tb}" + (" · SEE LOGS\\MONITOR.LOG" if tb else "")),
            ("STRATA", f"{w.sl.STRATA_URL} · {'ANSWERING' if s.get('up') else 'NOT ANSWERING'}"),
            ("PANEL CONTROLS", "ON CLICK ONLY · TWO CLICKS · LOGGED"),
        ]
        y = ry0 + 32
        for k, v in rows:
            w.text(rx0, y, k, w.f_tiny, TAN)
            w.text(rx0 + 130, y, w.fit(str(v).upper(), w.f_tiny, rx1 - rx0 - 130), w.f_tiny,
                   RED_HI if k.startswith("ERRORS") and tb else INK)
            y += 17
