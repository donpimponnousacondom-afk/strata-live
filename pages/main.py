"""01 MAIN ENGINEERING: the overview board (systems, the conversation, the last hour, the settings check, the log)."""
from __future__ import annotations

import datetime as dt
import math
import time

from lcars_theme import (BAR_ROW, BG, BLUE, GOLD, GREEN, GRID, INK, KIND_COLORS, LILAC, MUTED, PEACH, RED_HI, SKY,
                         TAN, fmt_k)
from pages.base import Page

KINDS = ("coding turn", "summary", "failed summary", "full re-read")


class MainPage(Page):
    code, title, sub, color = "01", "MAIN ENGINEERING", "overview", LILAC

    def __init__(self, w):
        super().__init__(w)
        self.log_offset = 0
        self.log_items: list = []
        self.selected = None

    def build(self, area) -> None:
        super().build(area)
        x0, y0, x1, y1 = area
        wb = max(300, int((x1 - x0) * 0.46))
        self.B = (x0, y0, x0 + wb, y1)
        self.C = (x0 + wb + 16, y0, x1, y1)
        bx0, by0, bx1, by1 = self.B
        self.tiles_r = (bx0, by0, bx1, by0 + 58)
        self.bars_r = (bx0, by0 + 68, bx1, by0 + 68 + 8 * BAR_ROW)
        gy = self.bars_r[3] + 10
        self.w.header(bx0, gy, bx1, "SENSOR TRACES · 2 MIN", PEACH)
        gh = max(56, (by1 - gy - 30 - 2 * 10) // 3)
        self.graphs = [(bx0, gy + 30 + i * (gh + 10), bx1, gy + 30 + i * (gh + 10) + gh) for i in range(3)]
        cx0, cy0, cx1, cy1 = self.C
        self.context_r = (cx0, cy0, cx1, cy0 + 62)
        y = cy0 + 70
        self.w.header(cx0, y, cx1, "DUTY CYCLE · LAST HOUR", GOLD)
        self.duty_r = (cx0, y + 28, cx1, y + 28 + 84)
        y += 28 + 92
        self.w.header(cx0, y, cx1, "SETTINGS CHECK", GREEN)
        self.diag_r = (cx0, y + 28, cx1, y + 28 + 122)
        y += 28 + 130
        self.w.header(cx0, y, cx1, "SHIP'S LOG · LATEST", PEACH)
        self.log_r = (cx0, y + 28, cx1, cy1 - 58)
        self.detail_r = (cx0, cy1 - 52, cx1, cy1)
        self.w.rect(*self.detail_r, "#0b0b10", outline=GRID)

    def draw(self, s) -> None:
        eng, live, hw = s.get("engine") or {}, s.get("live") or {}, s.get("hardware") or {}
        self.draw_tiles(s, live, hw)
        self.draw_bars(hw)
        self.draw_graphs()
        self.draw_context(s)
        self.draw_duty(s)
        self.draw_diag(s)
        self.draw_log()

    def draw_tiles(self, s, live, hw) -> None:
        last = s.get("last_request") or {}
        gen = live.get("state") == "generating"
        tok = live.get("tok_s") if gen else last.get("decode_tok_s")
        ctx = s.get("context")
        free = ((hw.get("gpu_mem_total") or 0) - (hw.get("gpu_mem_used") or 0)) / 1048576 \
            if hw.get("gpu_mem_total") else None
        temp = hw.get("gpu_temp")
        x0, y0, x1, _ = self.tiles_r
        self.w.tiles(x0, y0, x1, [
            (f"{tok:.1f}" if tok else "—", "TOK/S" if gen else "LAST TOK/S", GOLD, False),
            (fmt_k(ctx) if ctx else "—", "CONTEXT", BLUE, False),
            (f"{free:,.0f}" if free is not None else "—", "VRAM MiB", GREEN,
             free is not None and free < self.w.sl.VRAM_LOW_MIB),
            (f"{temp}°" if temp is not None else "—", "GPU °C", PEACH,
             temp is not None and temp >= self.w.sl.GPU_HOT_C)])

    def draw_bars(self, hw) -> None:
        x0, y0, x1, y1 = self.bars_r
        gib = 1073741824
        rows = [
            ("GPU LOAD", hw.get("gpu_util"), 100, "%", "{:.0f}"),
            ("VRAM USED", (hw.get("gpu_mem_used") or 0) / gib if hw.get("gpu_mem_total") else None,
             (hw.get("gpu_mem_total") or gib) / gib, "GiB", "{:.1f}"),
            ("GPU TEMP", hw.get("gpu_temp"), 95, "°C", "{:.0f}"),
            ("GPU POWER", hw.get("gpu_power"), hw.get("gpu_power_limit") or 200, "W", "{:.0f}"),
            ("CPU", hw.get("cpu"), 100, "%", "{:.0f}"),
            ("RAM", (hw.get("ram_used") or 0) / gib if hw.get("ram_total") else None,
             (hw.get("ram_total") or gib) / gib, "GB", "{:.1f}"),
            ("PCIe IN", hw.get("gpu_pcie_rx_mb"), 6000, "MB/s", "{:,.0f}"),
            ("SSD READ", hw.get("disk_read_mb"), 3000, "MB/s", "{:,.0f}"),
        ]
        for i, (name, v, vmax, unit, fmt) in enumerate(rows):
            y = y0 + i * BAR_ROW
            self.w.text(x0, y + 9, name, self.w.f_lab, TAN)
            frac = 0 if v is None else max(0.0, min(1.0, v / vmax))
            hot = {"GPU TEMP": (80 / 95, 85 / 95), "VRAM USED": (0.93, 0.97), "RAM": (0.9, 0.95)}.get(name, (0.8, 0.92))
            self.w.segbar(x0 + 92, y + 2, x1 - 86, y + 16, frac, BLUE, nseg=22, hot=hot)
            self.w.text(x1, y + 9, "—" if v is None else f"{fmt.format(v)} {unit}", self.w.f_small,
                        RED_HI if frac > hot[1] else INK, anchor="e")

    def draw_graphs(self) -> None:
        sl = self.w.sl
        with sl.lock:
            snaps = list(sl.samples)[-120:]
        series = [
            ("OUTPUT TOK/S", [((x.get("live") or {}).get("tok_s") or 0) if (x.get("live") or {}).get("state") ==
                              "generating" else 0 for x in snaps], None, GOLD),
            ("GPU LOAD %", [(x.get("hw") or {}).get("gpu_util") or 0 for x in snaps], 100, SKY),
            ("GPU TEMP °C", [(x.get("hw") or {}).get("gpu_temp") or 0 for x in snaps], 95, PEACH),
        ]
        for (name, vals, vmax, col), (x0, y0, x1, y1) in zip(series, self.graphs):
            n = len(vals)
            xs = [[1 - (n - 1 - i) / 119 for i in range(n)]] if n else None
            self.w.chart(x0, y0, x1, y1, [(vals, col)], vmax=vmax, title=name, xs=xs,
                         right=(f"{vals[-1]:.0f}" + (f" / PEAK {max(vals):.0f}" if max(vals) else "")) if vals else None)

    def draw_context(self, s) -> None:
        x0, y0, x1, y1 = self.context_r
        eng = s.get("engine") or {}
        ctx_max = eng.get("max_context") or 0
        cur, trig = s.get("context") or 0, s.get("trigger")
        self.w.text(x0, y0 + 8, "CONVERSATION", self.w.f_lab, BLUE)
        self.w.text(x1, y0 + 8, f"{cur:,} / {ctx_max:,} TOKENS" if ctx_max else f"{cur:,} TOKENS · LIMIT UNKNOWN", self.w.f_small, INK, anchor="e")
        by0, by1 = y0 + 22, y0 + 42
        nseg = 40
        segw = (x1 - x0) / nseg
        lit = round(min(1.0, cur / ctx_max) * nseg) if ctx_max else 0
        tseg = trig / ctx_max * nseg if trig and ctx_max else None
        for k in range(nseg):
            col = GRID
            if k < lit:
                col = RED_HI if tseg and k >= tseg else GOLD if tseg and k >= tseg - 3 else BLUE
            self.c.create_rectangle(x0 + k * segw + 1, by0, x0 + (k + 1) * segw - 2, by1, fill=col, outline="",
                                    tags=("dyn",))
        if trig and ctx_max:
            tx = x0 + (x1 - x0) * trig / ctx_max
            self.w.aa.polygon((tx - 6, by1 + 10, tx + 6, by1 + 10, tx, by1 + 2), RED_HI, ("dyn",), erode=0.4)
            left = trig - cur
            self.w.text(tx, by1 + 18, f"COMPACTION AT {trig:,}" + (f" · {left:,} TO GO" if left > 0 else ""),
                        self.w.f_tiny, RED_HI if left < 5000 else MUTED, anchor="e" if tx > (x0 + x1) / 2 else "w")

    def draw_duty(self, s) -> None:
        x0, y0, x1, y1 = self.duty_r
        c, aa = self.c, self.w.aa
        h = s.get("hour") or {}
        busy, total = h.get("busy") or {}, h.get("total") or 0
        share = h.get("share") or 0
        R, wdt = 30, 11
        cx, cy = x0 + R + 16, y0 + (y1 - y0) / 2
        box = (cx - R, cy - R, cx + R, cy + R)
        if total:
            ang = 90.0
            for k in KINDS:
                ext = 360.0 * busy.get(k, 0) / total
                if ext >= 3:
                    aa.arc(cx, cy, R, ang - ext + 1.5, ext - 3, wdt, KIND_COLORS[k], ("dyn",))
                ang -= ext
        else:
            aa.oval_ring(*box, wdt, GRID, ("dyn",))
        for k in range(24):
            a = math.radians(k * 15)
            r0, r1 = R + 9, R + 13
            aa.segment(cx + r0 * math.cos(a), cy - r0 * math.sin(a), cx + r1 * math.cos(a), cy - r1 * math.sin(a), 2,
                       TAN if k % 6 == 0 else MUTED, ("dyn",))
        hot = share > 0.25 and total > 600
        self.w.text(cx, cy, f"{share:.0%}", self.w.f_lab, RED_HI if hot else INK, anchor="center")
        lx = cx + R + 24
        y = y0 + 6
        for k in KINDS:
            c.create_rectangle(lx, y - 5, lx + 18, y + 5, fill=KIND_COLORS[k], outline="", tags=("dyn",))
            self.w.text(lx + 26, y, k.upper(), self.w.f_tiny, INK)
            self.w.text(x1, y, f"{busy.get(k, 0) / 60:.0f} MIN", self.w.f_tiny, INK, anchor="e")
            y += 15
        self.w.text(lx, y + 3, f"{share:.0%} ON COMPACTION · {h.get('saved', 0)} SAVED · {h.get('failed', 0)} FAILED",
                    self.w.f_tiny, RED_HI if hot else TAN)
        lc = s.get("last_compaction")
        if lc:
            ago = (time.time() - lc["ts"]) / 60
            self.w.text(lx, y + 18, f"LAST {dt.datetime.fromtimestamp(lc['ts']):%H:%M} · {ago:.0f} MIN AGO · "
                                    f"{lc['words']:,} WORDS", self.w.f_tiny, MUTED)

    def draw_diag(self, s) -> None:
        x0, y0, x1, y1 = self.diag_r
        y = y0
        for lv, txt in s.get("checks") or []:
            col = {"ok": GREEN, "warn": GOLD, "bad": RED_HI}[lv]
            self.w.pill(x0, y + 2, x0 + 30, y + 14, col, tags=("dyn",))
            item = self.w.text(x0 + 40, y + 8, txt, self.w.f_small, INK if lv == "ok" else col, width=x1 - x0 - 44)
            bb = self.c.bbox(item)
            y = (bb[3] if bb else y + 16) + 5
            if y > y1:
                break

    def draw_log(self) -> None:
        sl = self.w.sl
        x0, y0, x1, y1 = self.log_r
        with sl.lock:
            evs = list(reversed(list(sl.events)[-400:]))
        self.log_items = evs
        rows = max(1, int((y1 - y0) // 19))
        self.log_offset = max(0, min(self.log_offset, max(0, len(evs) - rows)))
        narrow = x1 - x0 < 480
        tfmt, kx, kw = ("%H:%M:%S", 60, 118) if narrow else ("%m.%d %H:%M:%S", 104, 150)
        for i, ev in enumerate(evs[self.log_offset:self.log_offset + rows]):
            y = y0 + i * 19 + 8
            key = (ev["ts"], ev["kind"])
            col = {"error": RED_HI, "warn": GOLD}.get(ev["level"], MUTED)

            def pick(k=key):
                self.selected = None if self.selected == k else k
            tags = self.w.on(f"log:{ev['ts']}:{ev['kind']}", pick)
            self.w.rect(x0, y - 9, x1, y + 9, "#1a1622" if self.selected == key else BG, tags)
            self.w.text(x0, y, dt.datetime.fromtimestamp(ev["ts"]).strftime(tfmt), self.w.f_small, LILAC, tags=tags)
            self.w.text(x0 + kx, y, self.w.fit(ev["kind"].upper(), self.w.f_small, kw - 6), self.w.f_small, col,
                        tags=tags)
            tx = x0 + kx + kw
            self.w.text(tx, y, self.w.fit(ev["text"].replace("\n", " "), self.w.f_small, x1 - tx), self.w.f_small,
                        INK if ev["level"] != "info" else MUTED, tags=tags)
        dx0, dy0, dx1, dy1 = self.detail_r
        if len(evs) > rows:
            self.w.text(dx1 - 8, dy0 + 10, f"{self.log_offset + 1}–{min(len(evs), self.log_offset + rows)} OF "
                                           f"{len(evs)} · SCROLL · F8 FOR THE FULL LOG", self.w.f_tiny, MUTED,
                        anchor="e")
        sel = next((e for e in evs if self.selected == (e["ts"], e["kind"])), None)
        self.w.text(dx0 + 10, dy0 + 30, (f"{sl.local(sel['ts'])} · {sel['source'].upper()} · {sel['kind'].upper()} ▸ "
                                         f"{sel['text']}") if sel else "SELECT A LOG ENTRY TO READ ALL OF IT",
                    self.w.f_tiny, INK if sel else MUTED, width=dx1 - dx0 - 20)

    def on_wheel(self, e) -> bool:
        x0, y0, x1, y1 = self.log_r
        if x0 <= e.x <= x1 and y0 <= e.y <= y1:
            self.log_offset = max(0, self.log_offset - (3 if e.delta > 0 else -3))
            return True
        return False
