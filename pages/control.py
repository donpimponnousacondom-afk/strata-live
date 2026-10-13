"""03 CORE CONTROL: start, stop and tune the engine.

Engine buttons take two clicks and are refused while a request runs. Live tuning (Strata's shared settings) is used
from the next request; saved settings (the run config) from the next full restart. Every change is logged."""
from __future__ import annotations

import datetime as dt
import os
import threading
import time

import strata_control as ctl
from lcars_theme import BG, BLUE, GOLD, GREEN, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, VIOLET
from pages.base import Page


class ControlPage(Page):
    code, title, sub, color = "03", "CORE CONTROL", "start, stop, tune", RED_HI

    def __init__(self, w):
        super().__init__(w)
        self.shared: dict = {}
        self.config: dict = {}
        self.cfg_file: dict = {}
        self.fetched = 0.0
        self.fetching = False

    def fetch(self, force=False) -> None:
        if self.fetching or (not force and time.time() - self.fetched < 5):
            return
        self.fetching = True

        def work():
            try:
                self.shared = ctl.get_shared()
                self.config = ctl.get_config()
                p = ctl.config_path()
                self.cfg_file = (self.w.sl.read_json(p, {}) or {}) if p else {}
            except Exception:
                pass
            finally:
                self.fetched = time.time()
                self.fetching = False
        threading.Thread(target=work, daemon=True).start()

    def build(self, area) -> None:
        super().build(area)
        self.fetch(force=True)

    def act(self, name, fn, *args) -> None:
        if not ctl.run_action(name, fn, *args):
            self.w.status = f"{ctl.action['name']} is still running"

        def later():
            time.sleep(1.5)
            self.fetch(force=True)
        threading.Thread(target=later, daemon=True).start()

    def draw(self, s) -> None:
        self.fetch()
        w = self.w
        x0, y0, x1, y1 = self.area
        live = s.get("live") or {}
        up = bool(s.get("up"))
        loaded = up and live.get("state") != "unloaded"
        running = ctl.action.get("name")
        why = ctl.busy() if up else None
        # engine buttons
        self.sub_header(x0, y0, x1, "ENGINE", RED_HI)
        by0, by1 = y0 + 28, y0 + 74
        n = 5
        bw = (x1 - x0 - (n - 1) * 8) / n
        free = not running and not why
        btns = [
            ("STANDBY", "unload · free the GPU", GOLD, lambda: self.act("standby", ctl.unload), loaded and free,
             "UNLOAD NOW"),
            ("ENGAGE", "load the model", GREEN, lambda: self.act("engage", ctl.load), up and not loaded and not running,
             None),
            ("RESTART ENGINE", "unload and load", PEACH, lambda: self.act("restart engine", ctl.restart_engine),
             loaded and free, "RESTART NOW"),
            ("FULL RESTART", "server too · new settings", ORANGE, lambda: self.act("full restart", ctl.full_restart),
             up and free, "RESTART ALL"),
        ]
        if up:
            btns.append(("SHUT DOWN", "stop strata", RED_HI, lambda: self.act("shut down", ctl.shutdown), free,
                         "SHUT DOWN NOW"))
        else:
            btns.append(("START STRATA", "run the .bat, minimized", GREEN,
                         lambda: self.act("start strata", ctl.start_server), not running, None))
        for i, (lab, sub, col, fn, en, conf) in enumerate(btns):
            bx = x0 + i * (bw + 8)
            w.button(bx, by0, bx + bw, by1, lab, col, fn, key="eng:" + lab, confirm=conf, enabled=en, sub=sub)
        state = ("OFFLINE" if not up else "STANDBY · MODEL UNLOADED" if not loaded else
                 (live.get("state") or "idle").upper())
        tree_note = f"STATE {state}"
        if running:
            tree_note += f" · {running.upper()} IN PROGRESS"
        elif why:
            tree_note += f" · CONTROLS WAIT: {why.upper()}"
        if ctl.pending_restart():
            tree_note += " · SAVED SETTINGS WAIT FOR A FULL RESTART"
        w.text(x0, by1 + 13, w.fit(tree_note, w.f_tiny, x1 - x0), w.f_tiny,
               GOLD if (why or running or ctl.pending_restart()) else MUTED)

        L, R = self.cols(2, area=(x0, by1 + 30, x1, y1))
        # live tuning
        lx0, ly0, lx1, _ = L
        self.sub_header(lx0, ly0, lx1, "LIVE TUNING · NEXT REQUEST", SKY)
        cfg = self.config
        cfg_default = {"temperature": cfg.get("sampling.temperature"), "top_p": cfg.get("sampling.top_p"),
                       "top_k": cfg.get("sampling.top_k"), "reasoning_budget_tokens": cfg.get("reasoning_budget_tokens"),
                       "max_tokens": None}
        y = ly0 + 30
        can = up and not running
        for key, (lab, lo, hi, step, dflt) in ctl.LIVE_KEYS.items():
            live_v = self.shared.get(key)
            base = cfg_default.get(key)
            value = live_v if live_v is not None else base if base is not None else dflt
            note = ("LIVE · CONFIG " + (f"{base:g}" if base is not None else "NONE")) if live_v is not None else \
                ("FROM THE CONFIG FILE" if base is not None else "NOT SET")
            if key == "max_tokens":
                note = "ONLY FOR APPS THAT SEND NO LIMIT · PI SENDS ITS OWN"
            fmt = "{:.2f}" if isinstance(step, float) else "{:,}"
            w.slider("live:" + key, lx0, y, lx1, lab, lo, hi, step, value, SKY if live_v is None else GOLD,
                     lambda v, k=key: self.act("live tuning", ctl.set_live, k, v), marker=base, fmt=fmt, note=note,
                     enabled=can)
            y += 44
        # thinking level
        eff_live = self.shared.get("reasoning_effort")
        eff_cfg = self.cfg_file.get("reasoning_effort")
        eff = eff_live or eff_cfg or "model default"
        w.text(lx0, y + 6, "THINKING", w.f_lab, GOLD if eff_live else SKY)
        w.text(lx1, y + 6, f"{eff.upper()}" + (" · LIVE" if eff_live else " · CONFIG" if eff_cfg else ""), w.f_lab,
               INK, anchor="e")
        ew = (lx1 - lx0 - 3 * 6) / 4
        for i, e in enumerate(ctl.EFFORTS):
            bx = lx0 + i * (ew + 6)
            cur = e == eff
            w.button(bx, y + 18, bx + ew, y + 40, e.upper(), GOLD if e == eff_live else SKY,
                     (lambda: None) if cur else (lambda e=e: self.act("live tuning", ctl.set_live, "reasoning_effort",
                                                                       e)),
                     key="eff:" + e, confirm=None if cur else "RE-READ", enabled=can, active=cur)
        w.text(lx0, y + 52, "CHANGING THE THINKING LEVEL COSTS PI ONE FULL RE-READ", w.f_tiny, MUTED)
        y += 66
        w.button(lx0, y, lx1, y + 24, "RESTORE DEFAULTS · CLEAR ALL LIVE TUNING", LILAC,
                 lambda: self.act("live tuning", ctl.clear_live), key="restore", confirm="CLEAR LIVE TUNING",
                 enabled=can and bool(self.shared))

        # saved settings and the engine's arguments
        rx0, ry0, rx1, ry1 = R
        self.sub_header(rx0, ry0, rx1, "SAVED SETTINGS · NEXT FULL RESTART", ORANGE)
        y = ry0 + 30
        defaults = {"vram_reserve_mib": 700, "engine_silence_s": 300, "idle_unload_s": 0}
        for key, (lab, lo, hi, step) in ctl.SAVED_KEYS.items():
            v = cfg.get(key)
            fmt = "{:.2f}" if isinstance(step, float) else "{:,}"
            note = ("IN THE CONFIG" if v is not None else
                    f"NOT SET · STRATA USES {defaults[key]:,}" if key in defaults else "NOT SET")
            w.slider("cfg:" + key, rx0, y, rx1, lab, lo, hi, step, v if v is not None else defaults.get(key),
                     ORANGE if v is not None else MUTED, lambda val, k=key: self.act("saved setting", ctl.set_saved, k,
                                                                                       val),
                     fmt=fmt, note=note, enabled=up and not running)
            y += 44
        lamp = ctl.pending_restart()
        w.pill(rx0, y + 2, rx0 + 30, y + 14, GOLD if lamp else GREEN, tags=("dyn",))
        w.text(rx0 + 40, y + 8, "PENDING · USE FULL RESTART TO APPLY" if lamp else "RUNNING WITH THE SAVED SETTINGS",
               w.f_tiny, GOLD if lamp else INK)
        y += 24
        self.sub_header(rx0, y, rx1, "ENGINE ARGUMENTS · READ-ONLY", MUTED)
        args = self.cfg_file.get("args") or []
        pairs = {}
        for i, a in enumerate(args):
            if isinstance(a, str) and a.startswith("--"):
                nxt = args[i + 1] if i + 1 < len(args) and not str(args[i + 1]).startswith("--") else "on"
                pairs[a[2:]] = os.path.basename(str(nxt)) if ("\\" in str(nxt) or "/" in str(nxt)) else str(nxt)
        show = ["max-context", "kv", "expert-cache", "prefill", "spec", "spec-min-p", "pcie-frac", "adapt-every",
                "adapt-swaps", "adapt-decay"]
        txt = " · ".join(f"{k} {pairs[k]}" for k in show if k in pairs)
        w.text(rx0, y + 24, txt.upper() or "—", w.f_tiny, INK, anchor="nw", width=rx1 - rx0)

        # the tuning log
        ty = max(ly0 + 30 + 5 * 44 + 66 + 34, y + 78)
        if ty + 60 < y1:
            self.sub_header(x0, ty, x1, "TUNING LOG", VIOLET)
            rows = list(reversed(ctl.tuning_history(20)))
            for i, r in enumerate(rows[:max(1, int((y1 - ty - 30) // 16))]):
                yy = ty + 32 + i * 16
                w.text(x0, yy, dt.datetime.fromtimestamp(r["ts"]).strftime("%m.%d %H:%M:%S"), w.f_tiny, LILAC)
                w.text(x0 + 96, yy, w.fit(r["text"], w.f_tiny, x1 - x0 - 96), w.f_tiny, INK if r.get("ok") else RED_HI)
            if not rows:
                w.text(x0, ty + 32, "NO CHANGES YET", w.f_tiny, MUTED)
