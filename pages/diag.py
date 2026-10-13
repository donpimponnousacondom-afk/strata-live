"""07 DIAGNOSTICS: diagnostic levels 5 (quick, read-only) to 1 (everything, with speed trials), and the speed trials.

Levels 5-3 only read. Levels 2 and 1 send test requests to Strata, like the speed-trial buttons: they run only when
Strata is idle, take two clicks, and cost pi one full re-read on its next turn (the conversation cache is off)."""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import statistics
import threading
import time

import strata_control as ctl
from lcars_theme import BG, BLUE, GOLD, GREEN, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, TRIAL_GREEN, VIOLET
from pages.base import Page

LEVELS = {
    5: ("QUICK", "settings and sensors · read-only"),
    4: ("LOGS", "+ today's warnings, config changes, monitor errors"),
    3: ("STRATA'S OWN VIEW", "+ /status, /slots, /settings, /config read back"),
    2: ("WITH A SPEED TRIAL", "+ a short speed trial · sends a test"),
    1: ("FULL WORKUP", "+ full speed trial and a bundle · sends tests"),
}
WORST = {"ok": 0, "warn": 1, "bad": 2}


class DiagPage(Page):
    code, title, sub, color = "07", "DIAGNOSTICS", "checks and speed trials", GREEN

    def __init__(self, w):
        super().__init__(w)
        self.results: list = []          # (level, part, text)
        self.ran = None                  # (level, time)

    # ------------------------------------------------------------ the checks
    def add(self, lv, part, text):
        self.results.append((lv, part, text))
        marks = self.w.scan["marks"] if self.w.scan else {}
        if part and WORST[lv] >= WORST.get(marks.get(part, "ok"), 0):
            marks[part] = lv
        time.sleep(0.25)                 # paced, so the scan reads like a scan

    def run_level(self, n: int) -> None:
        w, sl = self.w, self.w.sl
        self.results = []
        w.scan = {"start": time.time(), "dur": 9999, "marks": {}}
        s = dict(w.snap)
        hw, eng, live = s.get("hardware") or {}, s.get("engine") or {}, s.get("live") or {}
        last = s.get("last_request") or {}
        # level 5: settings and sensors
        for lv, txt in s.get("checks") or []:
            self.add(lv, "MATTER INJECTOR", txt)
        up = s.get("up")
        self.add("ok" if up and live.get("state") != "unloaded" else "bad" if not up else "warn", "DILITHIUM CHAMBER",
                 "Engine running" if up and live.get("state") != "unloaded" else
                 "Strata is not answering" if not up else "Engine on standby (unloaded)")
        temp = hw.get("gpu_temp")
        if temp is not None:
            self.add("ok" if temp < 80 else "warn" if temp < sl.GPU_HOT_C else "bad", "COOLANT",
                     f"GPU at {temp} °C (limit {sl.GPU_HOT_C} °C)")
        if hw.get("gpu_mem_total"):
            free = (hw["gpu_mem_total"] - (hw.get("gpu_mem_used") or 0)) / 1048576
            self.add("ok" if free >= 400 else "warn" if free >= sl.VRAM_LOW_MIB else "bad", "CONTAINMENT FIELD",
                     f"{free:,.0f} MiB of VRAM free")
        if last.get("hit_rate") is not None:
            h = last["hit_rate"]
            self.add("ok" if h >= 0.5 else "warn", "WARP COILS", f"Last request: {h:.0%} of experts already on the card")
        if last.get("pcie_share") is not None:
            p = last["pcie_share"]
            self.add("ok" if p < 0.35 else "warn", "ANTIMATTER INJECTOR",
                     f"Last request streamed {p:.0%} of its experts over PCIe "
                     f"(link Gen {hw.get('gpu_pcie_gen', '?')} of {hw.get('gpu_pcie_gen_max', '?')}, "
                     f"x{hw.get('gpu_pcie_width', '?')})")
        days = (sl.speed_summary(14) or {}).get(eng.get("model")) or []
        med = statistics.median([d["tok"] for d in days if d.get("tok")]) if any(d.get("tok") for d in days) else None
        if last.get("decode_tok_s") and med:
            r = last["decode_tok_s"] / med
            self.add("ok" if r >= 0.8 else "warn" if r >= 0.6 else "bad", "PLASMA FLOW",
                     f"Last request wrote {last['decode_tok_s']:.1f} tok/s; the 14-day median is {med:.1f}")
        if n <= 4:
            now = time.time()
            with sl.lock:
                evs = [e for e in sl.events if e["ts"] >= now - 86400]
            bad = [e for e in evs if e["level"] == "error"]
            warn = [e for e in evs if e["level"] == "warn" and e["ts"] >= now - 3600]
            kinds = {}
            for e in warn:
                kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
            self.add("bad" if bad else "ok", None, f"{len(bad)} errors in the last 24 h" +
                     (f" (last: {bad[-1]['kind']})" if bad else ""))
            self.add("warn" if warn else "ok", None, "No warnings in the last hour" if not warn else
                     "Warnings in the last hour: " + ", ".join(f"{k} ×{v}" for k, v in kinds.items()))
            hot = sum(1 for e in evs if e["kind"] == "gpu hot" and e["ts"] >= now - 3600)
            if hot:
                self.add("warn", "COOLANT", f"The GPU reached {sl.GPU_HOT_C} °C {hot} times in the last hour")
            cfg = [e for e in evs if e.get("source") == "config"]
            self.add("ok", None, f"{len(cfg)} config change(s) in the last 24 h" +
                     (f"; last: {cfg[-1]['text'][:90]}" if cfg else ""))
            try:
                with open(os.path.join(sl.LOG_DIR, "monitor.log"), encoding="utf-8", errors="replace") as f:
                    tb = f.read()[-200000:].count("Traceback")
            except OSError:
                tb = 0
            self.add("warn" if tb else "ok", None, f"Monitor errors (tracebacks) in monitor.log: {tb}")
        if n <= 3:
            for path in ("/status", "/slots", "/settings", "/config"):
                try:
                    code, body = ctl.http("GET", path, timeout=5)
                except Exception as e:
                    code, body = 0, {"error": str(e)}
                self.add("ok" if code == 200 else "bad", "DILITHIUM CHAMBER" if path == "/status" else None,
                         f"{path}: HTTP {code}" + (f" · {len(body.get('slots') or body)} slot(s)" if path == "/slots"
                                                   and code == 200 and isinstance(body, (dict, list)) else ""))
            shared = ctl.get_shared()
            self.add("warn" if shared else "ok", "MATTER INJECTOR",
                     "Live tuning active: " + ", ".join(f"{k}={v}" for k, v in shared.items()) if shared else
                     "No live tuning: Strata uses its config")
            if ctl.pending_restart():
                self.add("warn", None, "Saved settings changed since Strata started: a full restart applies them")
        if n <= 2:
            if ctl.busy():
                self.add("warn", "PLASMA FLOW", f"Speed trial skipped: {ctl.busy()}")
            else:
                ctl.action["note"] = "SPEED TRIAL RUN"
                rec = ctl.speed_trial("full" if n == 1 else "short")
                prev = [t for t in ctl.trials(50)[:-1] if t.get("decode_tok_s")]
                pm = statistics.median([t["decode_tok_s"] for t in prev]) if prev else None
                d = rec.get("decode_tok_s")
                lv = "ok" if not pm or not d or d >= 0.85 * pm else "warn"
                self.add(lv, "PLASMA FLOW", f"Speed trial: {d or 0:.1f} tok/s writing" +
                         (f", {rec['prefill_tok_s']:.0f} tok/s prefill" if rec.get("prefill_tok_s") else "") +
                         (f", first token {rec['ttft_s']:.1f} s" if rec.get("ttft_s") is not None else "") +
                         (f" (earlier trials: {pm:.1f})" if pm else ""))
        if n == 1:
            p = sl.make_bundle()
            self.add("ok", None, f"Diagnostics bundle saved: {os.path.basename(p)}")
        w.scan["dur"] = time.time() - w.scan["start"] + 0.4
        worst = max((WORST[r[0]] for r in self.results), default=0)
        self.ran = (n, time.time())
        rec = {"ts": time.time(), "level": n, "results": [{"level": a, "part": b, "text": c} for a, b, c in self.results]}
        with open(ctl.DIAG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        nb = sum(1 for r in self.results if r[0] == "bad")
        nw = sum(1 for r in self.results if r[0] == "warn")
        ctl.note("diagnostic", f"level {n} diagnostic: {len(self.results)} checks, {nw} to watch, {nb} failed",
                 ok=worst < 2)

    # ------------------------------------------------------------ drawing
    def draw(self, s) -> None:
        w = self.w
        L, R = self.cols(2)
        x0, y0, x1, y1 = L
        running = ctl.action.get("name")
        why = ctl.busy() if s.get("up") else "Strata is offline"
        self.sub_header(x0, y0, x1, "DIAGNOSTIC LEVELS", GREEN)
        y = y0 + 30
        for n in (5, 4, 3, 2, 1):
            name, desc = LEVELS[n]
            sends = n <= 2
            w.button(x0, y, x1, y + 38, f"LEVEL {n} · {name}", GREEN if n >= 3 else GOLD if n == 2 else ORANGE,
                     lambda n=n: ctl.run_action(f"diagnostic level {n}", self.run_level, n), key=f"lvl{n}",
                     confirm="SEND TEST · PI RE-READS" if sends else None,
                     enabled=not running and (not sends or not why), sub=desc)
            y += 44
        y += 6
        title = "RESULTS" + (f" · LEVEL {self.ran[0]} AT {dt.datetime.fromtimestamp(self.ran[1]):%H:%M:%S}"
                             if self.ran else "")
        self.sub_header(x0, y, x1, title, SKY)
        y += 30
        for lv, part, txt in list(self.results):
            if y > y1 - 10:
                break
            col = {"ok": GREEN, "warn": GOLD, "bad": RED_HI}[lv]
            w.pill(x0, y - 5, x0 + 26, y + 6, col, tags=("dyn",))
            item = w.text(x0 + 34, y, (f"{part.title()}: " if part else "") + txt, w.f_tiny,
                          INK if lv == "ok" else col, width=x1 - x0 - 36)
            bb = self.c.bbox(item)
            y = (bb[3] if bb else y + 8) + 9
        if not self.results:
            w.text(x0, y, "RUN A LEVEL: THE SCAN SWEEPS THE CORE AND MARKS EACH PART", w.f_tiny, MUTED)

        # speed trials
        x0, y0, x1, y1 = R
        self.sub_header(x0, y0, x1, "SPEED TRIALS", TRIAL_GREEN)
        bw = (x1 - x0 - 16) / 3
        for i, (kind, lab, sub) in enumerate((("short", "SHORT", "300 in · 256 out"),
                                              ("prefill", "PREFILL", "8,000 in · 32 out"),
                                              ("full", "FULL", "both ×3 · median"))):
            bx = x0 + i * (bw + 8)
            w.button(bx, y0 + 30, bx + bw, y0 + 72, lab, TRIAL_GREEN if kind != "full" else GOLD,
                     lambda k=kind: ctl.run_action("speed trial", ctl.speed_trial, k), key="trial:" + kind,
                     confirm="PI RE-READS NEXT TURN", enabled=not running and not why, sub=sub)
        w.text(x0, y0 + 86, w.fit(("WAITING: " + why.upper()) if why and not running else
                                  "RUNS ONLY WHEN STRATA IS IDLE · SAVED QUIETLY TO LOGS\\SPEED-TRIALS.JSONL",
                                  w.f_tiny, x1 - x0), w.f_tiny, GOLD if why else MUTED)
        tr = ctl.trials(60)
        last = tr[-1] if tr else {}
        w.tiles(x0, y0 + 100, x1, [
            (f"{last['decode_tok_s']:.1f}" if last.get("decode_tok_s") else "—", "WRITING TOK/S", GOLD, False),
            (f"{last['prefill_tok_s']:,.0f}" if last.get("prefill_tok_s") else "—", "PREFILL TOK/S", PEACH, False),
            (f"{last['ttft_s']:.1f}s" if last.get("ttft_s") is not None else "—", "FIRST TOKEN", SKY, False),
            (f"{last['hit_rate']:.0%}" if last.get("hit_rate") is not None else "—", "CACHE HIT", BLUE, False)])
        gy = y0 + 170
        gh = max(70, (y1 - gy - 120) / 2)
        dec = [t.get("decode_tok_s") for t in tr]
        pre = [t.get("prefill_tok_s") for t in tr]
        w.chart(x0, gy, x1, gy + gh, [(dec, GOLD)], title="WRITING TOK/S · EVERY TRIAL", fill=False,
                right=f"{len(tr)} TRIALS" if tr else "NO TRIALS YET")
        w.chart(x0, gy + gh + 8, x1, gy + 2 * gh + 8, [(pre, PEACH)], title="PREFILL TOK/S · EVERY TRIAL", fill=False)
        ty = gy + 2 * gh + 22
        rows = []
        for t in reversed(tr[-5:]):
            rows.append([dt.datetime.fromtimestamp(t["ts"]).strftime("%m.%d %H:%M"), t["trial"].upper(),
                         f"{t['decode_tok_s']:.1f}" if t.get("decode_tok_s") else "—",
                         f"{t['prefill_tok_s']:,.0f}" if t.get("prefill_tok_s") else "—",
                         ", ".join(f"{k}={v}" for k, v in (t.get("live_tuning") or {}).items()) or "config"])
        width = x1 - x0
        w.table(x0, ty, [("WHEN", 0, "w"), ("TRIAL", width * 0.22, "w"), ("WRITE", width * 0.44, "e"),
                         ("PREFILL", width * 0.60, "e"), ("TUNING", width * 0.64, "w")], rows, row_h=15,
                font=w.f_tiny)
