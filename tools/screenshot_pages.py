"""Open each console page in --selftest mode, take a screenshot of it at a given size, and report tracebacks.

    python tools\\screenshot_pages.py [out_dir] [pages, e.g. 1,3,6] [WxH, default 1280x780]

Needs Pillow for the screenshot (the Python you run this with, not the monitor's). The selftest windows are titled
"Strata Engineering (selftest)" so they never get mixed up with the running console. Each selftest also logs a
"started" event, like any start of the monitor."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYW = r"D:\strata\.venv\Scripts\python.exe"

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "out", "pages")
pages = [int(p) for p in (sys.argv[2] if len(sys.argv) > 2 else "1,2,3,4,5,6,7,8,9,10").split(",")]
w, h = (sys.argv[3] if len(sys.argv) > 3 else "1280x780").split("x")
os.makedirs(out, exist_ok=True)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
for n in pages:
    log = os.path.join(out, f"page{n}.txt")
    with open(log, "w", encoding="utf-8") as f:
        p = subprocess.Popen([PYW, "strata_monitor.pyw", "--selftest", "--selftest-seconds=11", f"--page={n}"],
                             cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
        time.sleep(7)
        subprocess.run([sys.executable, os.path.join(HERE, "grab_window.py"), os.path.join(out, f"page{n}.png"), w, h],
                       env=dict(env, GRAB_TITLE="(selftest)"), capture_output=True)
        p.wait()
    tb = open(log, encoding="utf-8", errors="replace").read().count("Traceback")
    print(f"page {n}: {'ok' if not tb else f'{tb} traceback(s), see ' + log}")
