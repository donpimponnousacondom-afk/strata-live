"""04 POWER & COOLANT: the hardware — the power flow from SSD to GPU, gauges, the last minute, the hardware events."""
from __future__ import annotations

import datetime as dt

from lcars_theme import (BG, BLUE, GOLD, GREEN, GRID, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, VIOLET,
                         mix)
from pages.base import Page

HW_KINDS = ("gpu hot", "vram low", "ram full", "unexpected shutdown")


class PowerPage(Page):
    code, title, sub, color = "04", "POWER & COOLANT", "hardware", SKY

    def build(self, area) -> None:
        super().build(area)
        self.L, self.R = self.cols(2, area=area)

    def node(self, x0, y0, x1, y1, title, lines, col):
        w = self.w
        w.rect(x0, y0, x0 + 8, y1, col, ("dyn",))
        self.c.create_rectangle(x0 + 8, y0, x1, y1, fill="#0b0b10", outline=mix(BG, col, 0.6), tags=("dyn",))
        w.text(x0 + 16, y0 + 11, title, w.f_lab, col)
        for i, ln in enumerate(lines):
            w.text(x0 + 16, y0 + 29 + i * 15, w.fit(ln, w.f_tiny, x1 - x0 - 22), w.f_tiny, INK)

    def conduit(self, x, y0, y1, mbs, col, label):
        w = self.w
        flow = min(1.0, (mbs or 0) / 1500)
        self.c.create_line(x, y0, x, y1, fill=mix(GRID, col, 0.25 + 0.75 * flow), width=6 + 6 * flow, tags=("dyn",))
        self.c.create_polygon(x - 9, y1 - 10, x + 9, y1 - 10, x, y1, fill=mix(GRID, col, 0.3 + 0.7 * flow),
                              outline="", tags=("dyn",))
        w.text(x + 16, (y0 + y1) / 2, f"{label} {mbs or 0:,.0f} MB/S", w.f_tiny, INK if flow > 0.02 else MUTED)

    def draw(self, s) -> None:
        w = self.w
        hw = s.get("hardware") or {}
        m = s.get("metrics") or {}
        hs = m.get("hardware_static") or {}
        gib = 1073741824
        x0, y0, x1, y1 = self.L
        self.sub_header(x0, y0, x1, "POWER FLOW · SSD ▸ RAM ▸ PCIe ▸ GPU", ORANGE)
        nh, gap = 62, 34
        y = y0 + 30
        ram_used, ram_tot = (hw.get("ram_used") or 0) / gib, (hw.get("ram_total") or 0) / gib
        vram_used, vram_tot = (hw.get("gpu_mem_used") or 0) / gib, (hw.get("gpu_mem_total") or 0) / gib
        nodes = [
            ("SSD · MODEL FILES", [f"READ {hw.get('disk_read_mb') or 0:,.1f} MB/S · WRITE "
                                   f"{hw.get('disk_write_mb') or 0:,.1f} MB/S"], TAN),
            ("SYSTEM RAM · ANTIMATTER PODS", [f"{ram_used:.1f} OF {ram_tot:.1f} GB · THE EXPERTS LIVE HERE",
                                              f"CPU {hw.get('cpu') or 0:.0f}% · {hs.get('cpu_name') or ''}"], LILAC),
            ("PCIe LINK · ANTIMATTER INJECTOR", [f"GEN {hw.get('gpu_pcie_gen', '—')} OF {hw.get('gpu_pcie_gen_max', '—')}"
                                                 f" · x{hw.get('gpu_pcie_width', '—')} (DROPS TO GEN 1 AT IDLE)",
                                                 f"IN {hw.get('gpu_pcie_rx_mb') or 0:,.0f} · OUT "
                                                 f"{hw.get('gpu_pcie_tx_mb') or 0:,.0f} MB/S"], PEACH),
            ("GPU · DILITHIUM CHAMBER", [f"{hs.get('gpu_name') or 'GPU'} · LOAD {hw.get('gpu_util', '—')}%",
                                         f"VRAM {vram_used:.1f} OF {vram_tot:.1f} GiB · {hw.get('gpu_temp', '—')} °C · "
                                         f"{hw.get('gpu_power') or 0:.0f} W"], SKY),
        ]
        flows = [(hw.get("disk_read_mb"), TAN, "READ"), (hw.get("gpu_pcie_rx_mb"), LILAC, "EXPERTS OUT"),
                 (hw.get("gpu_pcie_rx_mb"), PEACH, "INTO THE CARD")]
        for i, (title, lines, col) in enumerate(nodes):
            self.node(x0, y, x1, y + nh, title, lines, col)
            if i < len(flows):
                self.conduit(x0 + 40, y + nh, y + nh + gap, flows[i][0], flows[i][1], flows[i][2])
            y += nh + gap
        y -= gap
        # hardware events
        y += 16
        self.sub_header(x0, y, x1, "HARDWARE EVENTS", RED_HI)
        with w.sl.lock:
            evs = [e for e in list(w.sl.events) if e["kind"] in HW_KINDS or e.get("source") == "windows"][-30:]
        rows = list(reversed(evs))[:max(1, int((y1 - y - 34) // 16))]
        for i, e in enumerate(rows):
            yy = y + 32 + i * 16
            w.text(x0, yy, dt.datetime.fromtimestamp(e["ts"]).strftime("%m.%d %H:%M"), w.f_tiny, LILAC)
            w.text(x0 + 74, yy, e["kind"].upper(), w.f_tiny, RED_HI if e["level"] == "error" else GOLD)
            w.text(x0 + 190, yy, w.fit(e["text"], w.f_tiny, x1 - x0 - 190), w.f_tiny, INK)
        if not rows:
            w.text(x0, y + 32, "NONE", w.f_tiny, MUTED)

        # gauges and the last minute
        x0, y0, x1, y1 = self.R
        self.sub_header(x0, y0, x1, "GAUGES", GOLD)
        grid = (x1 - x0) / 4 < 110              # narrow: the four gauges go two by two
        r = min(44, (x1 - x0) / 4 - 14) if not grid else min(44, (x1 - x0) / 4 - 12)
        free = (hw.get("gpu_mem_total") or 0) - (hw.get("gpu_mem_used") or 0)
        free_mib = free / 1048576 if hw.get("gpu_mem_total") else None
        temp = hw.get("gpu_temp")
        plim = hw.get("gpu_power_limit") or 200
        gauges = [
            ((temp or 0) / 95, PEACH, "GPU °C", f"{temp if temp is not None else '—'}",
             temp is not None and temp >= w.sl.GPU_HOT_C),
            ((hw.get("gpu_power") or 0) / plim, GOLD, f"POWER W / {plim:.0f}", f"{hw.get('gpu_power') or 0:.0f}",
             False),
            ((free_mib or 0) / 2000, GREEN, "VRAM FREE MiB", f"{free_mib:,.0f}" if free_mib is not None else "—",
             free_mib is not None and free_mib < w.sl.VRAM_LOW_MIB),
            ((hw.get("cpu") or 0) / 100, LILAC, "CPU %", f"{hw.get('cpu') or 0:.0f}", False),
        ]
        per_row = 2 if grid else 4
        cw = (x1 - x0) / per_row
        for i, (fr, col, lab, val, hot) in enumerate(gauges):
            row, colm = divmod(i, per_row)
            w.gauge(x0 + cw * colm + cw / 2, y0 + 38 + r + row * (2 * r + 22), r, fr, col, lab, val, hot)
        gy = y0 + 38 + (2 if grid else 1) * (2 * r + 22) + 4
        self.sub_header(x0, gy, x1, "LAST MINUTE · STRATA'S SENSORS", SKY)
        hist = m.get("history") or {}
        series = [("GPU LOAD %", hist.get("gpu_util") or [], 100, SKY),
                  ("GPU TEMP °C", hist.get("gpu_temp") or [], 95, PEACH),
                  ("GPU POWER W", hist.get("gpu_power") or [], plim, GOLD),
                  ("CPU %", hist.get("cpu") or [], 100, LILAC),
                  ("PCIe IN MB/S", hist.get("gpu_pcie_rx_mb") or [], None, VIOLET)]
        top = gy + 30
        gh = max(46, (y1 - top - (len(series) - 1) * 8) / len(series))
        for i, (lab, vals, vmax, col) in enumerate(series):
            yy = top + i * (gh + 8)
            if yy + gh > y1 + 2:
                break
            vals = [v for v in vals if v is not None]
            w.chart(x0, yy, x1, yy + gh, [(vals, col)], vmax=vmax, title=lab,
                    right=(f"{vals[-1]:,.0f} · PEAK {max(vals):,.0f}" if vals else None),
                    hlines=[(85, RED_HI, "85 °C")] if "TEMP" in lab else ())
