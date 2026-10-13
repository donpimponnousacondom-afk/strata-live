"""02 WARP CORE: the engine in depth — where each request's experts came from, its speed, and the engine's settings."""
from __future__ import annotations

import datetime as dt

from lcars_theme import (BG, BLUE, COIL_CACHE, COIL_MISS, COIL_PCIE, GOLD, GREEN, GRID, INK, MUTED, ORANGE, PEACH,
                         RED_HI, SKY, TAN, coil_mix, fmt_k)
from pages.base import Page


def prefill_tok_s(r: dict):
    pr, ms = r.get("prompt_read") or 0, r.get("prompt_ms") or 0
    return pr / (ms / 1000) if ms and pr >= 200 else None


class WarpPage(Page):
    code, title, sub, color = "02", "WARP CORE", "the engine in depth", PEACH

    def build(self, area) -> None:
        super().build(area)
        self.L, self.R = self.cols(2, area=area)

    def draw(self, s) -> None:
        w = self.w
        with w.an.lock:
            reqs = [r for r in list(w.an.reqs)[-40:]]
        x0, y0, x1, y1 = self.L
        # coil map: one column per request, split by where its experts came from
        self.sub_header(x0, y0, x1, "COIL MAP · LAST 40 REQUESTS", ORANGE)
        my0, my1 = y0 + 30, y0 + 150
        n = max(1, len(reqs))
        cw = (x1 - x0) / 40
        for i, r in enumerate(reqs):
            mx = coil_mix(r)
            cx0 = x0 + (40 - n + i) * cw
            if not mx:
                self.c.create_rectangle(cx0 + 1, my0, cx0 + cw - 2, my1, fill=GRID, outline="", tags=("dyn",))
                continue
            yb = my1
            for frac, col in zip(mx, (COIL_CACHE, COIL_MISS, COIL_PCIE)):
                h = (my1 - my0) * frac
                if h >= 1:
                    self.c.create_rectangle(cx0 + 1, yb - h, cx0 + cw - 2, yb, fill=col, outline="", tags=("dyn",))
                yb -= h
        lx = x0
        for lab, col in (("CACHE HIT", COIL_CACHE), ("LOADED FROM RAM", COIL_MISS), ("STREAMED OVER PCIe", COIL_PCIE)):
            self.c.create_rectangle(lx, my1 + 10, lx + 12, my1 + 20, fill=col, outline="", tags=("dyn",))
            self.w.text(lx + 16, my1 + 15, lab, w.f_tiny, INK)
            lx += 26 + w.measure(lab, w.f_tiny)
        # per-request telemetry
        gy = my1 + 34
        gh = max(60, (y1 - gy - 2 * 10) / 3)
        tok = [r.get("decode_tok_s") if (r.get("output_tokens") or 0) >= 20 else None for r in reqs]
        pre = [prefill_tok_s(r) for r in reqs]
        hit = [(r.get("hit_rate") or 0) * 100 if r.get("hit_rate") is not None else None for r in reqs]
        xs = [[(40 - n + i) / 39 for i in range(n)]]
        last = lambda vals: next((v for v in reversed(vals) if v is not None), None)
        lt, lp, lh = last(tok), last(pre), last(hit)
        w.chart(x0, gy, x1, gy + gh, [(tok, GOLD)], title="WRITING TOK/S PER REQUEST", xs=xs, fill=False,
                right=f"LAST {lt:.1f}" if lt else None)
        w.chart(x0, gy + gh + 10, x1, gy + 2 * gh + 10, [(pre, PEACH)], title="PREFILL TOK/S (READS OVER 200 TOKENS)",
                xs=xs, fill=False, right=f"LAST {lp:,.0f}" if lp else None)
        w.chart(x0, gy + 2 * gh + 20, x1, gy + 3 * gh + 20, [(hit, SKY)], vmax=100, title="EXPERT CACHE HIT %",
                xs=xs, fill=False, right=f"LAST {lh:.0f}%" if lh is not None else None)

        # right: the last requests, the engine's settings, and the long trend
        x0, y0, x1, y1 = self.R
        self.sub_header(x0, y0, x1, "LAST 12 REQUESTS", BLUE)
        rows, colors = [], []
        for r in reversed(reqs[-12:]):
            p = r.get("prompt_tokens") or 0
            reused = (r.get("reused") or 0) / p if p else 0
            kind = w.an.kind(r)
            rows.append([dt.datetime.fromtimestamp(r["time"]).strftime("%H:%M:%S"), fmt_k(p), f"{reused:.0%}",
                         r.get("output_tokens") or 0, f"{r.get('decode_tok_s') or 0:.1f}",
                         f"{(r.get('hit_rate') or 0):.0%}", kind.upper()])
            colors.append(RED_HI if kind == "failed summary" else GOLD if kind != "coding turn" else INK)
        width = x1 - x0
        cols = [("TIME", 0, "w"), ("PROMPT", width * 0.24, "e"), ("REUSED", width * 0.36, "e"),
                ("OUT", width * 0.46, "e"), ("TOK/S", width * 0.57, "e"), ("HIT", width * 0.66, "e"),
                ("KIND", width * 0.70, "w")]
        w.table(x0, y0 + 34, cols, rows, row_h=16, colors=colors, font=w.f_tiny)
        ey = y0 + 34 + 13 * 16 + 14
        self.sub_header(x0, ey, x1, "ENGINE CONFIGURATION", GOLD)
        eng = s.get("engine") or {}
        tot = s.get("totals") or {}
        acc = (tot.get("drafts_accepted") or 0) / tot["drafts_offered"] if tot.get("drafts_offered") else None
        cfg = [
            ("ENGINE", f"{eng.get('version', '—')} · KV {str(eng.get('kv', '—')).upper()} · CONTEXT "
                       f"{eng.get('max_context') or 0:,}"),
            ("EXPERT CACHE", f"{eng.get('expert_slots', 0):,} SLOTS · {eng.get('expert_cache_mib', 0):,} MiB"),
            ("PCIe SHARE", f"{eng.get('pcie_frac', '—')} · POOL WORKERS {eng.get('pool_workers', '—')}"),
            ("SPECULATION", f"SPEC {eng.get('spec', '—')} · MTP {eng.get('mtp_max', '—')} · LOOKUP "
                            f"{eng.get('lookup', '—')} · MIN P {eng.get('spec_min_p', '—')}"),
            ("DRAFTS ACCEPTED", f"{acc:.0%} OF {tot.get('drafts_offered', 0):,} SINCE START" if acc is not None
             else "—"),
            ("CONVERSATION CACHE", "OFF (0 MiB)" if not eng.get("conversation_cache_mib") else
             f"{eng['conversation_cache_mib']:,} MiB · {eng.get('conversation_cache_slots')} SLOTS"),
            ("SPEED PROJECTION", str(eng.get("cvec") or "off").upper()),
            ("ARENA", f"{eng.get('arena_mib', 0):,} MiB · VRAM FREE AT START {eng.get('vram_free_mib', '—')} MiB"),
        ]
        yy = ey + 32
        for k, v in cfg:
            w.text(x0, yy, k, w.f_tiny, TAN)
            w.text(x0 + 128, yy, w.fit(v, w.f_small, x1 - x0 - 128), w.f_small, INK)
            yy += 17
        # speed by day
        sy = yy + 10
        if sy + 90 < y1:
            self.sub_header(x0, sy, x1, "SPEED BY DAY · MEDIAN", GREEN)
            model = eng.get("model")
            days = (w.sl.speed_summary(14) or {}).get(model) or []
            by0, by1 = sy + 30, y1 - 16
            if days:
                bw = (x1 - x0) / max(len(days), 1)
                top = max([d["tok"] or 0 for d in days] + [1]) * 1.15
                for i, d in enumerate(days):
                    v = d["tok"] or 0
                    hgt = (by1 - by0 - 14) * v / top
                    bx = x0 + i * bw
                    self.c.create_rectangle(bx + 2, by1 - hgt, bx + bw - 3, by1, fill=GREEN if i == len(days) - 1
                                            else BLUE, outline="", tags=("dyn",))
                    if bw > 26:
                        w.text(bx + bw / 2, by1 - hgt - 8, f"{v:.0f}", w.f_tiny, INK, anchor="center")
                    w.text(bx + bw / 2, by1 + 8, d["day"][5:], w.f_tiny, MUTED, anchor="center")
            else:
                w.text(x0, by0 + 10, "NO SPEED HISTORY YET", w.f_tiny, MUTED)
