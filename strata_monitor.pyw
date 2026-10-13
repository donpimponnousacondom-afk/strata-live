"""Strata Monitor: a desktop window over Strata Live's watchers. No web server, nothing to install.

It runs the same read-only watchers as strata_live.py (Strata's /metrics, its logs and settings, pi's sessions and
settings, the Windows event log) and adds the checks that would have caught the 2026-10-13 compaction loop:
  - settings check: pi's model, context and reply limit against what Strata serves; thinking off on both sides
  - compaction watch: how often pi compacts, how big its summary is, who wrote it, how much room is left after
  - where Strata's time went in the last hour: coding turns, summaries, failed summaries, full re-reads
  - thinking leak: pi says thinking is off, but replies carry thinking
  - a request history with timestamps (logs/requests-<date>.jsonl); Strata's own log has none

Start it with start-strata-monitor.bat (pythonw, no console). `--selftest` builds the window, refreshes it a few
times, prints what each panel shows and exits. Run only one of this and strata_live.py at a time: both write the same
event logs.
"""
from __future__ import annotations

import collections
import ctypes
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
MONITOR_LOG = os.path.join(LOG_DIR, "monitor.log")
SELFTEST = "--selftest" in sys.argv

# pythonw has no console: send prints and tracebacks to a file, before strata_live prints anything
if sys.stdout is None or sys.stderr is None or not SELFTEST:
    _out = open(MONITOR_LOG, "a", encoding="utf-8", buffering=1)
    if not SELFTEST:
        sys.stdout = sys.stderr = _out
    else:
        sys.stdout = sys.stdout or _out
        sys.stderr = sys.stderr or _out

# strata_live runs wevtutil for Windows events: keep that from flashing a console window
_run = subprocess.run


def _quiet_run(*a, **k):
    if os.name == "nt":
        k.setdefault("creationflags", 0x08000000)          # CREATE_NO_WINDOW
    return _run(*a, **k)


subprocess.run = _quiet_run

sys.path.insert(0, HERE)
import strata_live as sl  # noqa: E402  (after the stdout and subprocess fixes above)

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from version import VERSION  # noqa: E402  (the console's revision)
EXTENSION = os.path.join(sl.PI_AGENT, "extensions", "short-compaction.ts")

# warning levels for the new checks
LOOP_COMPACTIONS_PER_HOUR = 3       # this many compactions (saved or failed) in 60 minutes -> "compaction loop"
COMPACTION_SHARE_WARN = 0.25        # summaries + failed summaries + re-reads over this share of the last hour's work
COMPACTION_SHARE_MIN_BUSY_S = 600   # ... once Strata has worked at least this long in that hour
SUMMARY_CHARS_WARN = 20000          # a saved summary longer than this is growing out of hand
ROOM_AFTER_COMPACTION_WARN = 15000  # after a compaction, fewer free tokens than this before the next one
SYSTEM_PROMPT_TOKENS = 7000         # pi's system prompt and tools, about (measured 2026-10-13: 26K characters)

KINDS = ("coding turn", "summary", "failed summary", "full re-read")
KIND_COLORS = {"coding turn": "#3fae9e", "summary": "#d9946a", "failed summary": "#d9574b", "full re-read": "#c9b458"}


def log_exc(where: str) -> None:
    print(f"{sl.local(time.time())}  monitor error in {where}:\n{traceback.format_exc()}", flush=True)


def guarded(fn):
    """A watcher thread that logs a crash and starts again instead of dying silently."""
    def run():
        while True:
            try:
                fn()
                return
            except Exception:
                log_exc(fn.__name__)
                time.sleep(5)
    return run


def iso_ts(s: str) -> float | None:
    try:
        return dt.datetime.fromisoformat((s or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fmt_k(n) -> str:
    return "?" if n is None else f"{n / 1000:.0f}K" if n >= 10000 else f"{n:,}"


# ------------------------------------------------------------------ analysis (its own thread)
class Analyzer:
    def __init__(self):
        self.lock = threading.Lock()
        self.reqs: collections.deque = collections.deque(maxlen=5000)   # Strata requests seen, oldest first
        self.seen_times: set = set()
        self.saved_until = self._last_saved_time()
        self.compactions: list = []            # every pi compaction seen today, any session
        self.session = None                    # path of the pi session being followed
        self.offset = 0
        self.sess = {}                         # per-session state
        self.flags: dict = {}                  # warning -> time it last fired
        self.checks: list = []                 # (ok|warn|bad, text) for the settings panel
        self.bad_checks: set = set()
        self.snap: dict = {}                   # what the window draws
        self.prime_compactions()
        self.prime_requests()

    def prime_requests(self) -> None:
        """Today's saved request history, so 'last hour' is complete right after a restart."""
        f = os.path.join(LOG_DIR, f"requests-{dt.date.today():%Y-%m-%d}.jsonl")
        try:
            with open(f, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError):
            return
        for r in sorted(rows, key=lambda x: x.get("time") or 0):
            if r.get("time") in self.seen_times:
                continue
            self.seen_times.add(r.get("time"))
            r["prev_prompt"] = self.reqs[-1]["prompt_tokens"] if self.reqs else None
            self.reqs.append(r)

    def prime_compactions(self) -> None:
        """Compactions from every pi session touched in the last day, so the hour and the request kinds are right
        from the start (the live watch follows only the newest session)."""
        cutoff = time.time() - 86400
        for f in glob.glob(os.path.join(sl.PI_SESSIONS, "*", "*.jsonl")):
            try:
                if os.path.getmtime(f) < cutoff:
                    continue
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"compaction"' not in line:
                            continue
                        e = json.loads(line)
                        if e.get("type") != "compaction":
                            continue
                        u = e.get("usage") or {}
                        self.compactions.append({
                            "ts": iso_ts(e.get("timestamp")) or 0, "before": e.get("tokensBefore"),
                            "chars": len(e.get("summary") or ""), "words": len((e.get("summary") or "").split()),
                            "by_extension": bool(e.get("fromHook")), "in": u.get("input"), "out": u.get("output"),
                            "session": f})
            except (OSError, ValueError):
                pass
        self.compactions.sort(key=lambda c: c["ts"])

    # --- request history -------------------------------------------------
    def _last_saved_time(self) -> float:
        newest = 0.0
        for f in sorted(glob.glob(os.path.join(LOG_DIR, "requests-*.jsonl")))[-2:]:
            try:
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            newest = max(newest, json.loads(line).get("time") or 0)
                        except ValueError:
                            pass
            except OSError:
                pass
        return newest

    def take_requests(self, m: dict) -> None:
        model = (m.get("engine") or {}).get("model") or "?"
        for rq in sorted(m.get("requests") or [], key=lambda x: x.get("time") or 0):
            t = rq.get("time")
            if not t or t in self.seen_times:
                continue
            self.seen_times.add(t)
            prev = self.reqs[-1]["prompt_tokens"] if self.reqs else None
            r = dict(rq, model=model, prev_prompt=prev)
            self.reqs.append(r)
            if t > self.saved_until:
                self.saved_until = t
                day = dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d")
                with open(os.path.join(LOG_DIR, f"requests-{day}.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps({k: v for k, v in r.items() if k != "prev_prompt"}) + "\n")

    def kind(self, r: dict) -> str:
        p, reused, out = r.get("prompt_tokens") or 0, r.get("reused") or 0, r.get("output_tokens") or 0
        for c in self.compactions:              # a saved compaction: its usage matches the Strata request
            if c.get("in") == p and c.get("out") == out:
                return "summary"
        fresh = reused < 0.1 * p
        if fresh and r.get("finish") == "length" and r.get("prev_prompt") and p < 0.8 * r["prev_prompt"]:
            return "failed summary"
        if fresh and p > 30000:
            return "full re-read"
        return "coding turn"

    # --- pi session ------------------------------------------------------
    def follow_session(self) -> None:
        files = glob.glob(os.path.join(sl.PI_SESSIONS, "*", "*.jsonl"))
        if not files:
            return
        newest = max(files, key=os.path.getmtime)
        if newest != self.session:
            self.session, self.offset = newest, 0
            self.sess = {"file": newest, "folder": os.path.basename(os.path.dirname(newest)).strip("-"),
                         "model": None, "provider": None, "thinking": None, "thinking_blocks": 0, "assistant": 0,
                         "context": None, "after_compaction": None, "awaiting_after": False,
                         "ctx_hist": [], "tool_errors": {}, "edit_misses": 0, "replies_cut": 0, "tool_calls": 0}
        try:
            with open(newest, encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)
                data = f.read()
                self.offset = f.tell()
        except OSError:
            return
        live = self.sess.get("primed", False)
        for line in data.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            self.session_entry(e, live)
        self.sess["primed"] = True

    def session_entry(self, e: dict, live: bool) -> None:
        s, typ = self.sess, e.get("type")
        if typ == "model_change":
            s["provider"], s["model"] = e.get("provider"), e.get("modelId")
        elif typ == "thinking_level_change":
            s["thinking"] = e.get("thinkingLevel")
        elif typ == "compaction":
            u = e.get("usage") or {}
            c = {"ts": iso_ts(e.get("timestamp")) or time.time(), "before": e.get("tokensBefore"),
                 "chars": len(e.get("summary") or ""), "words": len((e.get("summary") or "").split()),
                 "by_extension": bool(e.get("fromHook")), "in": u.get("input"), "out": u.get("output"),
                 "session": s["file"]}
            if not any(x["ts"] == c["ts"] and x["session"] == c["session"] for x in self.compactions):
                self.compactions.append(c)
            s["awaiting_after"] = True
            if live:
                self.on_compaction(c)
        elif typ == "message":
            m = e.get("message") or {}
            if m.get("role") == "toolResult":
                s["tool_calls"] += 1
                if m.get("isError"):
                    name = m.get("toolName") or "?"
                    s["tool_errors"][name] = s["tool_errors"].get(name, 0) + 1
                    txt = json.dumps(m.get("content"))[:400].lower()
                    if name == "edit" and ("could not find" in txt or "not found" in txt or "no match" in txt):
                        s["edit_misses"] += 1
                return
            if m.get("role") != "assistant":
                return
            s["assistant"] += 1
            if m.get("stopReason") == "length":
                s["replies_cut"] += 1
            u = m.get("usage") or {}
            ctx = (u.get("input") or 0) + (u.get("cacheRead") or 0) + (u.get("output") or 0)
            if ctx:
                s["context"] = ctx
                s["ctx_hist"].append(((m.get("timestamp") or 0) / 1000 or time.time(), ctx))
                del s["ctx_hist"][:-2000]
                if s["awaiting_after"]:
                    s["awaiting_after"] = False
                    s["after_compaction"] = ctx
                    if live:
                        self.on_after_compaction(ctx)
            if any(isinstance(b, dict) and b.get("type") == "thinking" for b in m.get("content") or []):
                s["thinking_blocks"] += 1
                if live and s.get("thinking") == "off" and not s.get("leak_warned"):
                    s["leak_warned"] = True
                    sl.add_event("warn", "pi", "thinking leak",
                                 "pi's thinking is set to off, but this reply carries thinking. pi sends no thinking "
                                 "setting when it is off, and Strata then thinks by default. Add \"reasoning_effort\": "
                                 "\"none\" to the model's strata-*.json (D:\\strata\\CHANGES-2026-10-13.md).")

    def on_compaction(self, c: dict) -> None:
        by = "the short-summary extension" if c["by_extension"] else "pi's default summary"
        if c["chars"] > SUMMARY_CHARS_WARN:
            sl.add_event("warn", "pi", "summary too long",
                         f"pi's summary is {c['chars']:,} characters ({by}). A summary that keeps growing fills the "
                         f"room compaction is meant to free; a new pi session resets it.", c["ts"])
        if os.path.isfile(EXTENSION) and not c["by_extension"] and (self.sess.get("provider") == "local-llm"):
            sl.add_event("warn", "pi", "extension not used",
                         f"pi compacted with its default summary although {EXTENSION} is installed: the extension "
                         f"failed or fell back. Look for a 'Short compaction' warning in pi.", c["ts"])
        mine = [x for x in self.compactions if x["session"] == c["session"]]
        if len(mine) >= 3 and mine[-1]["chars"] > mine[-2]["chars"] > mine[-3]["chars"]:
            sl.add_event("warn", "pi", "summary growing",
                         f"pi's summary grew at each of the last three compactions "
                         f"({mine[-3]['chars']:,} -> {mine[-2]['chars']:,} -> {mine[-1]['chars']:,} characters).",
                         c["ts"])

    def on_after_compaction(self, ctx: int) -> None:
        trig = self.snap.get("trigger")
        if trig and trig - ctx < ROOM_AFTER_COMPACTION_WARN:
            sl.add_event("warn", "pi", "little room after compaction",
                         f"Right after compacting, the conversation is {ctx:,} tokens, only {trig - ctx:,} below "
                         f"where pi compacts ({trig:,}). pi will compact again within a few turns.")

    # --- settings check --------------------------------------------------
    def strata_config_for(self, model: str) -> tuple:
        for f in glob.glob(os.path.join(sl.STRATA_DIR, "strata-*.json")):
            c = sl.read_json(f, {}) or {}
            if c.get("model_name") == model:
                return f, c
        return None, {}

    def settings_checks(self, eng: dict) -> tuple:
        out = []
        model, ctx = eng.get("model"), eng.get("max_context") or 0
        s = self.sess
        pi_model = s.get("model") if s.get("provider") == "local-llm" else None
        p = sl.pi_model_settings(pi_model or None)
        pi_model = pi_model or p["model"]
        if model and pi_model and pi_model != model:
            out.append(("bad", f"pi is set to {pi_model}, but Strata is serving {model}. Requests will fail or "
                               f"use the wrong limits."))
        elif model:
            out.append(("ok", f"pi and Strata agree on the model: {model}"))
        cw, mt = p["context_window"], p["max_tokens"]
        if cw and ctx and cw > ctx:
            out.append(("bad", f"pi thinks the context is {cw:,} tokens; Strata has {ctx:,}. Long conversations "
                               f"will be refused."))
        elif cw and ctx:
            out.append(("ok", f"Context: pi {cw:,}, Strata {ctx:,}"))
        if mt and ctx and mt > ctx * 0.5:
            out.append(("bad" if mt >= ctx - 2000 else "warn",
                        f"pi's reply limit ({mt:,}) is over half of Strata's context ({ctx:,}): it leaves little "
                        f"room for the prompt."))
        trig = (cw or ctx) - p["reserve"] if (cw or ctx) else None
        if trig:
            out.append(("ok", f"pi compacts at {trig:,} tokens and keeps the last {p['keep']:,}"))
            last = next((c for c in reversed(self.compactions)), None)
            summ = (last["chars"] / 3.5) if last else 0
            floor = SYSTEM_PROMPT_TOKENS + p["keep"] + summ
            if floor > trig - ROOM_AFTER_COMPACTION_WARN:
                out.append(("warn", f"After a compaction about {floor:,.0f} tokens remain, close to the "
                                    f"{trig:,} trigger: pi will compact again soon after."))
        cfg_path, cfg = self.strata_config_for(model) if model else (None, {})
        thinking = s.get("thinking")
        if thinking == "off" and cfg_path and not cfg.get("reasoning_effort"):
            out.append(("warn", f"pi's thinking is off, but {os.path.basename(cfg_path)} has no reasoning_effort: "
                                f"Strata will think anyway."))
        elif cfg.get("reasoning_effort"):
            out.append(("ok", f"Strata's thinking for requests without a setting: {cfg['reasoning_effort']}"))
        out.append(("ok" if os.path.isfile(EXTENSION) else "warn",
                    "Short-summary extension installed" if os.path.isfile(EXTENSION)
                    else "Short-summary extension not installed: pi's summaries can grow without limit"))
        return out, trig

    # --- the hour ---------------------------------------------------------
    def last_hour(self, now: float) -> dict:
        busy = dict.fromkeys(KINDS, 0.0)
        failed = 0
        for r in self.reqs:
            if (r.get("time") or 0) < now - 3600:
                continue
            k = self.kind(r)
            busy[k] += r.get("duration_s") or 0
            failed += k == "failed summary"
        saved = [c for c in self.compactions if c["ts"] >= now - 3600]
        total = sum(busy.values())
        share = (busy["summary"] + busy["failed summary"] + busy["full re-read"]) / total if total else 0
        return {"busy": busy, "total": total, "share": share, "saved": len(saved), "failed": failed}

    def alarms(self, hour: dict, now: float) -> None:
        def once(key, hours=1.0):
            if now - self.flags.get(key, 0) < hours * 3600:
                return False
            self.flags[key] = now
            return True
        n = hour["saved"] + hour["failed"]
        if n >= LOOP_COMPACTIONS_PER_HOUR and once("loop"):
            sl.add_event("warn", "pi", "compaction loop",
                         f"pi compacted {n} times in the last hour ({hour['failed']} failed). Compaction is "
                         f"probably freeing too little room: see D:\\strata\\Handoff.md, 3.7.")
        if hour["total"] >= COMPACTION_SHARE_MIN_BUSY_S and hour["share"] > COMPACTION_SHARE_WARN and once("share"):
            sl.add_event("warn", "pi", "compaction time",
                         f"{hour['share']:.0%} of Strata's work in the last hour went to compaction: summaries "
                         f"and re-reading the conversation after them.")

    # --- the loop ---------------------------------------------------------
    def step(self) -> None:
        now = time.time()
        with sl.lock:
            m = sl.state.get("metrics") or {}
            up = sl.state.get("up")
        if m:
            self.take_requests(m)
        self.follow_session()
        eng = m.get("engine") or {}
        checks, trig = self.settings_checks(eng) if eng else ([("bad", "Strata is not answering")], None)
        for level, text in checks:
            key = text.split(":")[0][:40]
            if level == "bad" and key not in self.bad_checks:
                self.bad_checks.add(key)
                sl.add_event("warn", "monitor", "settings mismatch", text)
        self.bad_checks &= {t.split(":")[0][:40] for lv, t in checks if lv == "bad"}
        hour = self.last_hour(now)
        self.alarms(hour, now)
        last_c = self.compactions[-1] if self.compactions else None
        last_r = self.reqs[-1] if self.reqs else None
        with self.lock:
            self.snap = {"up": up, "engine": eng, "live": m.get("live") or {}, "hardware": m.get("hardware") or {},
                         "checks": checks, "trigger": trig, "session": dict(self.sess), "hour": hour,
                         "last_compaction": last_c, "requests_saved": self.saved_until, "last_request": last_r,
                         "totals": m.get("totals") or {}, "metrics": m, "compactions_today": sum(1 for c in self.compactions if c["ts"] >= dt.datetime.combine(
                             dt.date.today(), dt.time()).timestamp()),
                         "context": self.sess.get("context") or (
                             ((last_r.get("prompt_tokens") or 0) + (last_r.get("output_tokens") or 0))
                             if last_r else None)}

    def run(self) -> None:
        while True:
            try:
                self.step()
            except Exception:
                log_exc("analyzer")
            time.sleep(2.0)


# ------------------------------------------------------------------ the window
BG, PANEL, INK, MUTED, LINE = "#16191b", "#1f2326", "#e3e6e4", "#9aa3a0", "#30363a"
OK, WARN, BAD, ACCENT = "#4cc38a", "#e3b341", "#ef6a5e", "#5cc0b2"


class Window:
    def __init__(self, an: Analyzer):
        self.an = an
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Strata Monitor")
        self.root.geometry("820x760")
        self.root.minsize(560, 520)
        self.root.configure(bg=BG)
        self.top = tk.BooleanVar(value=False)
        self.seen_events = None
        self.alert_until = 0.0
        f = ("Segoe UI", 10)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK, font=("Segoe UI", 9),
                     rowheight=22, borderwidth=0)
        st.configure("Treeview.Heading", background=BG, foreground=MUTED, font=("Segoe UI", 9), borderwidth=0)
        st.map("Treeview", background=[("selected", "#2c4a46")])
        st.configure("TButton", background=PANEL, foreground=INK, borderwidth=1, font=("Segoe UI", 9))
        st.map("TButton", background=[("active", "#2a3034")])
        st.configure("TCheckbutton", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        st.map("TCheckbutton", background=[("active", BG)])

        pad = {"padx": 14}
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", pady=(12, 4), **pad)
        self.dot_strata = tk.Canvas(head, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot_strata.pack(side="left")
        self.lbl_strata = tk.Label(head, text="Strata", bg=BG, fg=INK, font=("Segoe UI Semibold", 10))
        self.lbl_strata.pack(side="left", padx=(6, 16))
        self.dot_pi = tk.Canvas(head, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot_pi.pack(side="left")
        self.lbl_pi = tk.Label(head, text="pi", bg=BG, fg=INK, font=("Segoe UI Semibold", 10))
        self.lbl_pi.pack(side="left", padx=(6, 0))
        ttk.Checkbutton(head, text="Always on top", variable=self.top,
                        command=lambda: self.root.attributes("-topmost", self.top.get())).pack(side="right")

        self.lbl_now = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=f, anchor="w")
        self.lbl_now.pack(fill="x", **pad)

        self.section("Conversation size")
        self.gauge = tk.Canvas(self.root, height=24, bg=BG, highlightthickness=0)
        self.gauge.pack(fill="x", **pad)
        self.lbl_gauge = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.lbl_gauge.pack(fill="x", **pad)

        self.section("Last hour: where Strata's time went")
        self.hourbar = tk.Canvas(self.root, height=20, bg=BG, highlightthickness=0)
        self.hourbar.pack(fill="x", **pad)
        self.lbl_hour = tk.Label(self.root, text="", bg=BG, fg=INK, font=("Segoe UI", 9), anchor="w",
                                 justify="left")
        self.lbl_hour.pack(fill="x", **pad)
        self.lbl_comp = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                                 justify="left")
        self.lbl_comp.pack(fill="x", **pad)

        self.section("Hardware")
        self.lbl_hw = tk.Label(self.root, text="", bg=BG, fg=INK, font=("Segoe UI", 9), anchor="w")
        self.lbl_hw.pack(fill="x", **pad)

        self.section("Settings check")
        self.checks = tk.Text(self.root, height=6, bg=BG, fg=INK, font=("Segoe UI", 9), relief="flat",
                              wrap="word", highlightthickness=0, cursor="arrow")
        self.checks.pack(fill="x", **pad)
        for tag, col in (("ok", OK), ("warn", WARN), ("bad", BAD)):
            self.checks.tag_configure(tag, foreground=col)

        self.section("Events")
        box = tk.Frame(self.root, bg=BG)
        box.pack(fill="both", expand=True, **pad)
        self.tree = ttk.Treeview(box, columns=("time", "kind", "text"), show="headings", height=8)
        for col, w, stretch in (("time", 120, False), ("kind", 160, False), ("text", 500, True)):
            self.tree.heading(col, text=col.capitalize(), anchor="w")
            self.tree.column(col, width=w, stretch=stretch, anchor="w")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("error", foreground=BAD)
        self.tree.tag_configure("warn", foreground=WARN)
        self.tree.tag_configure("info", foreground=MUTED)
        self.tree.bind("<<TreeviewSelect>>", self.show_event)
        self.lbl_detail = tk.Label(self.root, text="Select an event to read all of it.", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 9), anchor="w", justify="left", wraplength=780)
        self.lbl_detail.pack(fill="x", pady=(4, 0), **pad)
        self.root.bind("<Configure>", lambda e: self.lbl_detail.configure(
            wraplength=max(300, self.root.winfo_width() - 40)))

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", pady=10, **pad)
        ttk.Button(bar, text="Open logs folder", command=lambda: os.startfile(LOG_DIR)).pack(side="left")
        ttk.Button(bar, text="Make diagnostics bundle", command=self.bundle).pack(side="left", padx=8)
        self.lbl_status = tk.Label(bar, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.lbl_status.pack(side="left", padx=8)
        self.event_rows: dict = {}

    def section(self, title: str) -> None:
        tk.Label(self.root, text=title.upper(), bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold"),
                 anchor="w").pack(fill="x", padx=14, pady=(12, 3))

    @staticmethod
    def dot(c: tk.Canvas, color: str) -> None:
        c.delete("all")
        c.create_oval(1, 1, 11, 11, fill=color, outline=color)

    def show_event(self, _e=None) -> None:
        sel = self.tree.selection()
        if sel and sel[0] in self.event_rows:
            ev = self.event_rows[sel[0]]
            self.lbl_detail.configure(text=f"{sl.local(ev['ts'])}  [{ev['source']}] {ev['kind']}: {ev['text']}",
                                      fg=INK)

    def bundle(self) -> None:
        self.lbl_status.configure(text="Making the bundle ...")

        def work():
            try:
                p = sl.make_bundle()
                self.root.after(0, lambda: self.lbl_status.configure(text=f"Saved {os.path.basename(p)}"))
                os.startfile(os.path.dirname(p))
            except Exception:
                log_exc("bundle")
                self.root.after(0, lambda: self.lbl_status.configure(text="The bundle failed; see monitor.log"))
        threading.Thread(target=work, daemon=True).start()

    def flash(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    def refresh(self) -> None:
        try:
            self.draw()
        except Exception:
            log_exc("refresh")
        self.root.after(1000, self.refresh)

    def draw(self) -> None:
        with self.an.lock:
            s = dict(self.an.snap)
        if not s:
            return
        eng, live, hw, sess = s["engine"], s["live"], s["hardware"], s["session"]
        up = s["up"]
        self.dot(self.dot_strata, OK if up else BAD)
        self.lbl_strata.configure(text=(f"Strata  {eng.get('model', '?')} · {eng.get('max_context') or 0:,} · "
                                        f"engine {eng.get('version', '?')}") if up and eng else "Strata not answering")
        bad = any(lv == "bad" for lv, _ in s["checks"])
        self.dot(self.dot_pi, BAD if bad else WARN if any(lv == "warn" for lv, _ in s["checks"]) else OK)
        self.lbl_pi.configure(text=(f"pi  …{sess.get('folder', '?')[-28:]} · thinking {sess.get('thinking') or '?'}"
                                    if sess else "pi  no session found"))
        doing = sl.strata_doing() if up else "Strata is not answering on " + sl.STRATA_URL
        tok = live.get("tok_s")
        self.lbl_now.configure(text=f"Now: {doing}" + (f"   {tok:.1f} tok/s" if tok else ""))

        # conversation gauge
        ctx_max = eng.get("max_context") or 1
        cur, trig = s.get("context") or 0, s.get("trigger")
        g = self.gauge
        g.delete("all")
        w = max(100, g.winfo_width())
        g.create_rectangle(0, 6, w, 18, fill=PANEL, outline=LINE)
        frac = min(1.0, cur / ctx_max)
        near = trig and cur > trig - 5000
        g.create_rectangle(0, 6, int(w * frac), 18, fill=WARN if near else ACCENT, outline="")
        if trig:
            x = int(w * min(1.0, trig / ctx_max))
            g.create_line(x, 0, x, 24, fill=BAD, width=2)
        self.lbl_gauge.configure(text=(f"{cur:,} of {ctx_max:,} tokens"
                                       + (f" · pi compacts at {trig:,} (red line)" if trig else "")
                                       + (f" · {trig - cur:,} to go" if trig and cur < trig else "")))

        # last hour
        h = s["hour"]
        hb = self.hourbar
        hb.delete("all")
        w = max(100, hb.winfo_width())
        hb.create_rectangle(0, 2, w, 18, fill=PANEL, outline=LINE)
        x = 0.0
        if h["total"]:
            for k in KINDS:
                dx = w * h["busy"][k] / h["total"]
                if dx >= 1:
                    hb.create_rectangle(x, 2, x + dx, 18, fill=KIND_COLORS[k], outline=BG)
                x += dx
        parts = [f"{k} {h['busy'][k] / 60:.0f} min" for k in KINDS if h["busy"][k] >= 30]
        self.lbl_hour.configure(text=(f"Busy {h['total'] / 60:.0f} min: " + ", ".join(parts)
                                      + f" · compaction share {h['share']:.0%}") if h["total"]
                                else "Strata has done no work in the last hour.")
        lc = s.get("last_compaction")
        txt = f"Compactions in the last hour: {h['saved']} saved, {h['failed']} failed"
        if lc:
            ago = (time.time() - lc["ts"]) / 60
            txt += (f".  Last: {dt.datetime.fromtimestamp(lc['ts']).strftime('%H:%M')} ({ago:.0f} min ago), "
                    f"{fmt_k(lc['before'])} tokens into a {lc['words']:,}-word summary by "
                    f"{'the extension' if lc['by_extension'] else 'pi default'}")
            if sess.get("after_compaction") and lc.get("session") == sess.get("file"):
                txt += f"; {sess['after_compaction']:,} tokens right after"
        self.lbl_comp.configure(text=txt)

        # hardware
        mib = 1048576
        if hw.get("gpu_mem_total"):
            free = (hw["gpu_mem_total"] - (hw.get("gpu_mem_used") or 0)) / mib
            ram = (hw.get("ram_used") or 0) / mib / 1024, (hw.get("ram_total") or 0) / mib / 1024
            self.lbl_hw.configure(
                text=(f"VRAM {free:,.0f} MiB free · GPU {hw.get('gpu_util', '?')}% · {hw.get('gpu_temp', '?')} °C · "
                      f"{hw.get('gpu_power') or 0:.0f} W · RAM {ram[0]:.1f} of {ram[1]:.1f} GB · CPU "
                      f"{hw.get('cpu') or 0:.0f}%"),
                fg=BAD if free < sl.VRAM_LOW_MIB or (hw.get("gpu_temp") or 0) >= sl.GPU_HOT_C else INK)
        else:
            self.lbl_hw.configure(text="No hardware readings", fg=MUTED)

        # settings check
        self.checks.configure(state="normal")
        self.checks.delete("1.0", "end")
        for lv, text in s["checks"]:
            self.checks.insert("end", {"ok": "✓  ", "warn": "!  ", "bad": "✕  "}[lv], lv)
            self.checks.insert("end", text + "\n")
        self.checks.configure(state="disabled", height=max(3, min(8, len(s["checks"]))))

        # events, newest first
        with sl.lock:
            evs = list(sl.events)[-300:]
        key = (len(evs), evs[-1]["ts"] if evs else 0)
        if key != self.seen_events:
            new_bad = self.seen_events is not None and any(
                e["level"] in ("warn", "error") and e["ts"] > (self.seen_events[1] or 0) for e in evs)
            self.seen_events = key
            self.tree.delete(*self.tree.get_children())
            self.event_rows.clear()
            for ev in reversed(evs):
                iid = self.tree.insert("", "end", values=(
                    dt.datetime.fromtimestamp(ev["ts"]).strftime("%m-%d %H:%M:%S"), ev["kind"],
                    ev["text"].replace("\n", " ")), tags=(ev["level"],))
                self.event_rows[iid] = ev
            if new_bad:
                self.alert_until = time.time() + 30
                self.flash()
                self.root.bell()
        self.root.title(("⚠ " if time.time() < self.alert_until else "") + "Strata Monitor")

    def text_dump(self) -> str:
        rows = [f"strata: {self.lbl_strata.cget('text')}", f"pi:     {self.lbl_pi.cget('text')}",
                f"now:    {self.lbl_now.cget('text')}", f"gauge:  {self.lbl_gauge.cget('text')}",
                f"hour:   {self.lbl_hour.cget('text')}", f"comp:   {self.lbl_comp.cget('text')}",
                f"hw:     {self.lbl_hw.cget('text')}", "checks:"]
        rows += ["   " + line for line in self.checks.get("1.0", "end").strip().splitlines()]
        rows.append("events (newest 6):")
        for iid in self.tree.get_children()[:6]:
            rows.append("   " + "  ".join(str(v) for v in self.tree.item(iid, "values"))[:180])
        return "\n".join(rows)


# ------------------------------------------------------------------ start
def single_instance() -> bool:
    if os.name != "nt":
        return True
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\StrataMonitor")
    return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS


def web_version_running() -> bool:
    """Strata Live's web version answers /api/state with its own fields; anything else on the port is not it."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{sl.PORT}/api/state", timeout=1.0) as r:
            body = json.loads(r.read().decode("utf-8"))
        return isinstance(body, dict) and "events" in body and "version" in body
    except Exception:
        return False


def main() -> None:
    if not SELFTEST and not single_instance():
        ctypes.windll.user32.MessageBoxW(None, "Strata Monitor is already running.", "Strata Monitor", 0x40)
        return
    sl.load_incidents()
    if not sl.speed_load():
        sl.speed_backfill_from_logs()
    for target in (sl.poll_strata, sl.watch_files, sl.watch_configs, sl.watch_windows, sl.cleanup):
        threading.Thread(target=guarded(target), daemon=True).start()
    an = Analyzer()
    threading.Thread(target=guarded(an.run), daemon=True).start()
    if web_version_running():
        sl.add_event("warn", "monitor", "two monitors",
                     f"Strata Live's web version is also running (port {sl.PORT}). Both write the same event logs, "
                     f"so events are logged twice. Close one of them.")
    sl.add_event("info", "monitor", "started", f"Strata Monitor {VERSION} (engineering console): logs in {LOG_DIR}")
    if "--classic" not in sys.argv:
        from lcars_window import LcarsWindow
        page = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--page=")), 1)) - 1
        win = LcarsWindow(an, sl, start_page=page,
                          title="Strata Engineering (selftest)" if SELFTEST else "Strata Engineering")
        if SELFTEST:
            # stays on top for --selftest-seconds (default 8), then prints the readouts and the window's place
            secs = float(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--selftest-seconds=")), 8))
            win.root.attributes("-topmost", True)

            def finish():
                win.draw_dynamic()
                print(win.text_dump(), flush=True)
                win.root.destroy()
            win.root.after(int(secs * 1000), finish)

            def where():
                print(f"GEOMETRY {win.root.winfo_rootx()} {win.root.winfo_rooty()} {win.root.winfo_width()} "
                      f"{win.root.winfo_height()}", flush=True)
            win.root.after(2500, where)
        win.start()
        return
    win = Window(an)
    if SELFTEST:
        def finish():
            win.draw()
            print(win.text_dump(), flush=True)
            win.root.destroy()
        for i in range(1, 7):
            win.root.after(1000 * i, win.draw)
        win.root.after(7500, finish)
        win.root.mainloop()
        return
    win.root.after(500, win.refresh)
    win.root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_exc("main")
        raise
