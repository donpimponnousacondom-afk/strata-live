"""tkprobe: measure how smoothly a tkinter app renders, without changing its code. Standard library only.

Runs your app in-process, wraps the methods you name with timers, records the animation loop's frame pacing,
optionally drags the window size around like a user would, counts canvas items, and prints a report.

    python tkprobe.py --methods "app_window:Window.redraw,app_window:Window.build" \\
                      --frame "app_window:Window.animate" --seconds 12 -- app.py --some-app-flag
    python tkprobe.py --frame "ui:Main.tick" --resize 1280x780:1920x1080 --census -- app.pyw
    python tkprobe.py --profile "ui:Main.redraw" --seconds 8 -- app.py

    --methods   module:Class.method list to time (median / p95 / max per call). For a class defined in the script
                you run, write __main__:Class.method (it's patched when the app creates its Tk root)
    --frame     the method your animation loop calls once per frame: gives fps, gap p95/max, late frames
    --resize    A:B  after warm-up, 61 sizes 16 ms apart, A to B and back (a drag); reports how long the
                app took to keep up (ideal ~1000 ms) and fps meanwhile
    --census    count canvas items by type at the end (item churn is the usual cost of a redraw)
    --profile   cProfile one method, print the top functions by own time at the end
    --seconds   how long to run before reporting and closing (default 10)

Budgets that feel smooth (see the skill's perf playbook): animation frame <= 4 ms of work, frame gaps steady at the
nominal interval (no gap > 1.5x), a once-a-second data redraw <= 20 ms, a 60-step drag done in < 1.5 s.
"""
from __future__ import annotations

import argparse
import importlib
import os
import runpy
import sys
import time
import tkinter as tk

TIMES: dict = {}
TICKS: list = []


def _stats(v):
    v = sorted(v)
    return f"n={len(v)} median={v[len(v) // 2]:.1f}ms p95={v[int(len(v) * 0.95)]:.1f}ms max={v[-1]:.1f}ms"


def pacing() -> str:
    gaps = sorted((b - a) * 1000 for a, b in zip(TICKS, TICKS[1:]))
    TICKS[:] = TICKS[-1:]
    if len(gaps) < 2:
        return "no frames recorded"
    nominal = gaps[len(gaps) // 2]
    # Windows' timer ticks every ~15.6 ms, so after(33) alternates ~31/47 ms: only count a gap late past one tick
    limit = max(nominal * 1.5, nominal + 17)
    late = sum(1 for g in gaps if g > limit)
    return (f"fps={len(gaps) / (sum(gaps) / 1000):.1f} gap median={nominal:.0f}ms p95={gaps[int(len(gaps) * 0.95)]:.0f}ms "
            f"max={gaps[-1]:.0f}ms late(>{limit:.0f}ms)={late}/{len(gaps)}")


def timings() -> str:
    out = [f"{k}: {_stats(v)}" for k, v in TIMES.items() if v]
    for k in TIMES:
        TIMES[k] = []
    return "\n  ".join(out) or "no calls"


def _resolve(spec: str):
    mod, _, attr = spec.partition(":")
    cls_name, _, meth = attr.rpartition(".")
    obj = sys.modules["__main__"] if mod == "__main__" else importlib.import_module(mod)
    for part in cls_name.split(".") if cls_name else []:
        obj = getattr(obj, part)
    return obj, meth


def wrap(spec: str, frame: bool = False, prof=None) -> None:
    if spec in TIMES:                            # already wrapped: don't count calls twice
        return
    owner, meth = _resolve(spec)
    fn = getattr(owner, meth)
    TIMES.setdefault(spec, [])

    def timed(*a, **k):
        t = time.perf_counter()
        if frame:
            TICKS.append(t)
        if prof:
            prof.enable()
        try:
            return fn(*a, **k)
        finally:
            if prof:
                prof.disable()
            TIMES[spec].append((time.perf_counter() - t) * 1000)
    setattr(owner, meth, timed)


def _alive(root) -> bool:
    try:
        return bool(root.winfo_exists())
    except tk.TclError:                          # destroyed
        return False


def census(root) -> str:
    kinds: dict = {}
    stack = [root]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                kinds[w.type(item)] = kinds.get(w.type(item), 0) + 1
    return f"{sum(kinds.values())} items {kinds}"


def resize_sweep(root, a, b, done, steps=30, every_ms=16):
    sizes = [(round(a[0] + (b[0] - a[0]) * k / steps), round(a[1] + (b[1] - a[1]) * k / steps))
             for k in range(steps + 1)]
    sizes += sizes[-2::-1]
    timings(), pacing()
    t0 = time.perf_counter()

    def step(i=0):
        if i < len(sizes):
            root.geometry("%dx%d" % sizes[i])
            root.after(every_ms, lambda: step(i + 1))
            return
        t_last = time.perf_counter()

        def idle():       # the app has processed everything queued by the drag
            lag = (time.perf_counter() - t_last) * 1000
            root.after(250, lambda: done(f"{len(sizes)} sizes in {(t_last - t0) * 1000:.0f} ms (ideal "
                                         f"{len(sizes) * every_ms} ms), idle {lag:.0f} ms after the last"))
        root.after_idle(idle)
    step()


def main() -> int:
    argv = sys.argv[1:]
    if "--" not in argv:
        print(__doc__)
        return 2
    cut = argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="")
    ap.add_argument("--frame", default="")
    ap.add_argument("--resize", default="")
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--profile", default="")
    ap.add_argument("--seconds", type=float, default=10.0)
    a = ap.parse_args(argv[:cut])
    target, target_args = os.path.abspath(argv[cut + 1]), argv[cut + 2:]
    sys.path.insert(0, os.path.dirname(target))       # the app's imports work; its working directory is yours

    prof = None
    if a.profile:
        import cProfile
        prof = cProfile.Profile()
    specs = list(dict.fromkeys(s.strip() for s in a.methods.split(",") if s.strip()))
    for extra in (a.frame, a.profile):
        if extra and extra not in specs:
            specs.append(extra)

    def patch(main_too: bool) -> None:
        for spec in specs:
            if spec.startswith("__main__:") == main_too:
                wrap(spec, frame=(spec == a.frame), prof=prof if spec == a.profile else None)

    patch(False)                                       # importable modules: now, before the app imports them
    roots = []
    _init = tk.Tk.__init__

    def init(self, *x, **k):
        _init(self, *x, **k)
        patch(True)                                    # classes in the script itself exist by now
        if roots and _alive(roots[-1]):
            return                                     # a second root while the first lives: not the main one
        roots.append(self)                             # (a splash root that was destroyed is replaced)

        def report_and_quit():
            print(f"[tkprobe] steady state:\n  {timings()}\n  frames: {pacing()}", flush=True)
            if a.census:
                print(f"[tkprobe] canvas: {census(self)}", flush=True)
            if prof:
                import pstats
                pstats.Stats(prof).sort_stats("tottime").print_stats(15)
            self.destroy()

        def after_warmup():
            if a.resize:
                lo, hi = ((int(v) for v in s.lower().split("x")) for s in a.resize.split(":"))
                lo, hi = tuple(lo), tuple(hi)

                def done(msg):
                    print(f"[tkprobe] resize: {msg}\n  during it: {timings()}\n  frames: {pacing()}", flush=True)
                    self.geometry("%dx%d" % lo)
                    self.after(int(a.seconds * 500), report_and_quit)
                resize_sweep(self, lo, hi, done)
            else:
                timings(), pacing()          # drop the start-up, measure the steady state
                self.after(int(a.seconds * 1000), report_and_quit)
        self.after(int(a.seconds * 400), after_warmup)

    tk.Tk.__init__ = init
    sys.argv = [target] + target_args
    runpy.run_path(target, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
