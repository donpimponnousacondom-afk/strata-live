"""05 COMMS · PI LINK: the conversation pi sends to Strata — its size over time, compactions, and what went wrong."""
from __future__ import annotations

import datetime as dt
import os
import time

from lcars_theme import BG, BLUE, GOLD, GREEN, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, VIOLET, fmt_k
from pages.base import Page


class CommsPage(Page):
    code, title, sub, color = "05", "COMMS · PI LINK", "pi and the conversation", BLUE

    def draw(self, s) -> None:
        w = self.w
        x0, y0, x1, y1 = self.area
        sess = s.get("session") or {}
        hist = list(sess.get("ctx_hist") or [])
        trig = s.get("trigger")
        ctx_max = (s.get("engine") or {}).get("max_context") or 131072
        self.sub_header(x0, y0, x1, "CONVERSATION SIZE · THIS SESSION", BLUE)
        ch0, ch1 = y0 + 30, y0 + 30 + max(150, int((y1 - y0) * 0.36))
        with w.an.lock:
            comps = [c for c in w.an.compactions if c.get("session") == sess.get("file")]
        if len(hist) >= 2:
            t0, t1 = hist[0][0], max(hist[-1][0], time.time() - 30)
            span = max(60.0, t1 - t0)
            xs = [(t - t0) / span for t, _ in hist]
            vals = [v for _, v in hist]
            top = max([ctx_max * 0.8] + vals) * 1.05
            marks = [((c["ts"] - t0) / span, GOLD) for c in comps if t0 <= c["ts"] <= t1]
            w.chart(x0, ch0, x1, ch1, [(vals, BLUE)], vmax=top, xs=[xs], fill=True, marks=marks,
                    title=f"{dt.datetime.fromtimestamp(t0):%H:%M} → NOW · {len(hist)} REPLIES",
                    right=f"NOW {vals[-1]:,} TOKENS",
                    hlines=[(trig, RED_HI, f"PI COMPACTS AT {trig:,}")] if trig else ())
        else:
            w.chart(x0, ch0, x1, ch1, [], title="WAITING FOR PI'S FIRST REPLY IN THIS SESSION", title_col=MUTED)
        L, R = self.cols(2, area=(x0, ch1 + 14, x1, y1))
        # compactions
        lx0, ly0, lx1, ly1 = L
        self.sub_header(lx0, ly0, lx1, "COMPACTIONS · TODAY", GOLD)
        with w.an.lock:
            today = [c for c in w.an.compactions if c["ts"] >= time.time() - 86400][-12:]
        rows, colors = [], []
        for c in reversed(today):
            rows.append([dt.datetime.fromtimestamp(c["ts"]).strftime("%H:%M"), fmt_k(c.get("before")),
                         f"{c['words']:,}", "EXTENSION" if c["by_extension"] else "PI DEFAULT"])
            colors.append(INK if c["by_extension"] else GOLD)
        width = lx1 - lx0
        w.table(lx0, ly0 + 32, [("TIME", 0, "w"), ("AT TOKENS", width * 0.36, "e"), ("SUMMARY WORDS", width * 0.66, "e"),
                                ("WRITTEN BY", width * 0.70, "w")],
                rows[:max(1, int((ly1 - ly0 - 50) // 16))], row_h=16, colors=colors, font=w.f_tiny)
        if not rows:
            w.text(lx0, ly0 + 50, "NONE IN THE LAST 24 HOURS", w.f_tiny, MUTED)
        # counters and pi's settings
        rx0, ry0, rx1, ry1 = R
        self.sub_header(rx0, ry0, rx1, "LINK STATUS · THIS SESSION", SKY)
        with w.sl.lock:
            evs = [e for e in w.sl.events if e["ts"] >= time.time() - 86400 and e.get("source") == "pi"]
        timeouts = sum(1 for e in evs if e["kind"] == "timeout")
        te = sess.get("tool_errors") or {}
        leaks = sess.get("thinking_blocks", 0) if sess.get("thinking") == "off" else 0
        tiles = [
            (f"{sess.get('assistant', 0):,}", "REPLIES", BLUE, False),
            (f"{sess.get('edit_misses', 0)}", "EDIT MISSES", PEACH, sess.get("edit_misses", 0) >= 10),
            (f"{sum(te.values())}", "TOOL ERRORS", GOLD, False),
            (f"{leaks}", "THINK LEAKS", LILAC, leaks > 0),
        ]
        w.tiles(rx0, ry0 + 30, rx1, tiles)
        tiles2 = [
            (f"{sess.get('replies_cut', 0)}", "REPLIES CUT", ORANGE, sess.get("replies_cut", 0) > 0),
            (f"{timeouts}", "TIMEOUTS 24H", RED_HI if timeouts else GREEN, False),
            (f"{sess.get('tool_calls', 0):,}", "TOOL CALLS", SKY, False),
            (fmt_k(sess.get("after_compaction")) if sess.get("after_compaction") else "—", "AFTER COMP",
             GREEN, False),
        ]
        w.tiles(rx0, ry0 + 96, rx1, tiles2)
        p = w.sl.pi_model_settings(sess.get("model"))
        yy = ry0 + 172
        info = [
            ("SESSION", (sess.get("folder") or "—").replace("C--Users-tom-Desktop-", "")),
            ("MODEL", f"{sess.get('model') or p.get('model') or '—'} · THINKING {str(sess.get('thinking')).upper()}"),
            ("PI WINDOW", f"{(p.get('context_window') or 0):,} · REPLY LIMIT {(p.get('max_tokens') or 0):,}"),
            ("COMPACTION", f"RESERVE {p.get('reserve', 0):,} · KEEP {p.get('keep', 0):,} · AT {trig or 0:,}"),
            ("TOOL ERRORS", " · ".join(f"{k} {v}" for k, v in sorted(te.items(), key=lambda kv: -kv[1])) or "NONE"),
        ]
        for k, v in info:
            if yy > ry1 - 4:
                break
            w.text(rx0, yy, k, w.f_tiny, TAN)
            w.text(rx0 + 86, yy, w.fit(str(v).upper(), w.f_tiny, rx1 - rx0 - 86), w.f_tiny, INK)
            yy += 16
