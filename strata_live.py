"""strata_live.py - a read-only live monitor for Strata.

It never changes Strata or pi. It only reads:
  - Strata's own status page   http://127.0.0.1:8080/metrics   (once a second)
  - Strata's log files          D:\\strata\\strata-*.log         (new lines only)
  - pi's session files          %USERPROFILE%\\.pi\\agent\\sessions\\**\\*.jsonl  (new lines only)
  - Strata and pi settings     strata-*.json, engine\\BUILD.json, pi's models.json / settings.json (for changes)
  - Windows' System event log   graphics driver resets, hardware errors, low memory, unexpected shutdowns

It writes what it finds to D:\\strata-live\\logs\\ and serves a live page at http://127.0.0.1:8099/.
Optional: STRATA_RELAY_PORT=8090 also starts a pass-through relay that times each request between an app and
Strata (point the app at the relay to use it). Standard library only. Stop it with Ctrl+C or by closing its window.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import hashlib
import http.client
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.3"
HERE = os.path.dirname(os.path.abspath(__file__))
STRATA_URL = os.environ.get("STRATA_URL", "http://127.0.0.1:8080")
STRATA_DIR = os.environ.get("STRATA_DIR", r"D:\strata")
PI_AGENT = os.path.join(os.path.expanduser("~"), ".pi", "agent")
PI_SESSIONS = os.path.join(PI_AGENT, "sessions")
PI_COMPACT_RESERVE = 16384          # pi's default compaction.reserveTokens (pi compacts at context - this)
PI_KEEP_RECENT = 20000              # pi's default compaction.keepRecentTokens
STRATA_API_KEY = os.environ.get("STRATA_API_KEY", "")   # only needed when Strata runs with --api-key
PORT = int(os.environ.get("STRATA_LIVE_PORT", "8099"))
RELAY_PORT = int(os.environ.get("STRATA_RELAY_PORT", "0") or 0)   # 0 = relay off
LOG_DIR = os.path.join(HERE, "logs")
BACKFILL_HOURS = 48                 # past pi errors and Windows events shown at start, so the page is not empty

# warning levels (each must last HW_SUSTAIN_S seconds before it is logged, so a brief spike is ignored)
VRAM_LOW_MIB = 250                  # free graphics memory; Strata's log calls ~400 MiB "little room" on Windows
RAM_FULL_PCT = 95                   # above this Windows starts paging to disk
GPU_HOT_C = 85
HW_SUSTAIN_S = 10
RUNAWAY_TOKENS = 8000               # one reply still going past this many tokens
TOOL_CALL_SLOW_S = 300              # one tool call still being written after this long
SLOWDOWN_PCT = 25                   # today's median writing speed this much below the last 7 days' -> warning
SLOWDOWN_MIN_REPLIES = 10           # replies of 100+ tokens needed in a day before it is compared
RELAY_SILENCE_WARN_S = (60, 240)    # the relay warns when Strata sends nothing for this long mid-request

# incident recordings: the snapshots around a problem, saved to logs/incidents/
INCIDENT_BEFORE_S = 300
INCIDENT_AFTER_S = 60
INCIDENT_WARN_KINDS = {"server", "vram low", "ram full", "gpu hot", "runaway reply", "slow tool call",
                       "relay silence", "windows low memory"}

# housekeeping
LOG_KEEP_DAYS = 30                  # events-<date>.log, relay-<date>.log and incidents older than this are deleted
BUNDLE_KEEP_DAYS = 14
JSONL_MAX_MB = 20                   # events.jsonl is trimmed to its newer half past this size

INCIDENT_DIR = os.path.join(LOG_DIR, "incidents")
BUNDLE_DIR = os.path.join(LOG_DIR, "bundles")
SPEED_FILE = os.path.join(LOG_DIR, "speed-history.json")
CONFIG_STATE_FILE = os.path.join(LOG_DIR, "config-state.json")
WINEV_FILE = os.path.join(LOG_DIR, "windows-events.json")

for d in (INCIDENT_DIR, BUNDLE_DIR):
    os.makedirs(d, exist_ok=True)
lock = threading.Lock()
events: collections.deque = collections.deque(maxlen=600)
samples: collections.deque = collections.deque(maxlen=1800)   # ~30 min of one-second snapshots
req_log: collections.deque = collections.deque(maxlen=3000)   # finished Strata requests, for linking pi errors
relay_log: collections.deque = collections.deque(maxlen=60)   # finished relayed requests
relay_active: dict = {}                                       # id -> record of requests passing through now
winev_seen: list = []                                         # Windows events found (newest last)
state: dict = {"up": False, "metrics": None, "since": time.time(), "counts": collections.Counter(),
               "incidents": [], "open_incident": None, "winev": None}


def local(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def hhmm(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def add_event(level: str, source: str, kind: str, text: str, ts: float | None = None, write: bool = True,
              extra: dict | None = None) -> None:
    ts = ts or time.time()
    ev = {"ts": ts, "level": level, "source": source, "kind": kind, "text": text}
    if extra:
        ev.update(extra)
    with lock:
        events.append(ev)
        state["counts"][kind] += 1
    if write:
        day = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        line = f"{local(ts)}  {level.upper():5}  [{source}] {kind}: {text}"
        with open(os.path.join(LOG_DIR, f"events-{day}.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        with open(os.path.join(LOG_DIR, "events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        print(line.encode(sys.stdout.encoding or "utf-8", "replace").decode(sys.stdout.encoding or "utf-8"),
              flush=True)
        if level == "error" or (level == "warn" and kind in INCIDENT_WARN_KINDS):
            record_incident(ev)


def later(delay: float, fn, *args) -> None:
    t = threading.Timer(delay, fn, args=args)
    t.daemon = True
    t.start()


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def pi_model_settings(model_id: str | None = None) -> dict:
    """pi's settings for one local-llm model, as pi resolves them: compaction reserve and keep from the model's
    override, else the global compaction block, else pi's defaults; contextWindow and maxTokens from models.json."""
    s = read_json(os.path.join(PI_AGENT, "settings.json"), {}) or {}
    mj = read_json(os.path.join(PI_AGENT, "models.json"), {}) or {}
    model_id = model_id or str(s.get("defaultModel") or "")
    comp = s.get("compaction") or {}
    ov = (comp.get("modelOverrides") or {}).get(f"local-llm/{model_id}") or {}
    models = (((mj.get("providers") or {}).get("local-llm") or {}).get("models") or [])
    entry = next((m for m in models if m.get("id") == model_id), {})
    return {"model": model_id,
            "reserve": ov.get("reserveTokens", comp.get("reserveTokens", PI_COMPACT_RESERVE)),
            "keep": ov.get("keepRecentTokens", comp.get("keepRecentTokens", PI_KEEP_RECENT)),
            "context_window": entry.get("contextWindow"), "max_tokens": entry.get("maxTokens"),
            "known": bool(entry), "default_provider": s.get("defaultProvider"), "default_model": s.get("defaultModel")}


def pi_compacts_at(ctx: int, model_id: str | None = None) -> int | None:
    """Where pi compacts: its own contextWindow for the model (Strata's when pi has none) minus reserveTokens."""
    p = pi_model_settings(model_id)
    window = p["context_window"] or ctx
    return window - p["reserve"] if window else None


def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ incident recordings
def record_incident(ev: dict) -> None:
    """Save the snapshots from INCIDENT_BEFORE_S before to INCIDENT_AFTER_S after `ev`. Problems within 90 s of an
    open recording join it instead of starting another."""
    if ev["ts"] < time.time() - samples.maxlen + INCIDENT_AFTER_S:
        return                                   # older than what is still in memory
    with lock:
        inc = state["open_incident"]
        if inc and ev["ts"] - inc["ts"] < 90:
            inc["events"].append(ev)
            return
        stamp = dt.datetime.fromtimestamp(ev["ts"]).strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in ev["kind"]).strip("-")
        inc = {"name": f"{stamp}-{slug}", "ts": ev["ts"], "events": [ev]}
        state["open_incident"] = inc
    later(max(0.0, inc["ts"] + INCIDENT_AFTER_S - time.time()), write_incident, inc)


def write_incident(inc: dict) -> None:
    with lock:
        if state["open_incident"] is inc:
            state["open_incident"] = None
        snaps = [s for s in samples if inc["ts"] - INCIDENT_BEFORE_S <= s["t"] <= inc["ts"] + INCIDENT_AFTER_S]
        m = state["metrics"] or {}
        relayed = [r for r in list(relay_log) + list(relay_active.values())
                   if r.get("start", 0) <= inc["ts"] + INCIDENT_AFTER_S
                   and (r.get("end") or time.time()) >= inc["ts"] - INCIDENT_BEFORE_S]
    body = {"name": inc["name"], "ts": inc["ts"], "trigger": inc["events"][0], "events": inc["events"],
            "engine": m.get("engine"), "hardware_static": m.get("hardware_static"), "samples": snaps,
            "relay": relayed}
    path = os.path.join(INCIDENT_DIR, inc["name"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    summary = {"name": inc["name"], "ts": inc["ts"], "kind": inc["events"][0]["kind"],
               "text": inc["events"][0]["text"], "n_events": len(inc["events"]), "n_samples": len(snaps)}
    with lock:
        state["incidents"].append(summary)
    add_event("info", "monitor", "incident saved",
              f"Saved {len(snaps)} s of snapshots around '{summary['kind']}' to {path}. Replay it on the page.")


def load_incidents() -> None:
    found = []
    for path in sorted(glob.glob(os.path.join(INCIDENT_DIR, "*.json"))):
        b = read_json(path)
        if not isinstance(b, dict) or "name" not in b:
            continue
        tr = b.get("trigger") or {}
        found.append({"name": b["name"], "ts": b.get("ts", 0), "kind": tr.get("kind", "?"), "text": tr.get("text", ""),
                      "n_events": len(b.get("events") or []), "n_samples": len(b.get("samples") or [])})
    with lock:
        state["incidents"] = found


# ------------------------------------------------------------------ what Strata was doing
def strata_doing(at: float | None = None) -> str:
    """What Strata was doing at time `at` (the nearest snapshot), in words."""
    with lock:
        snaps = list(samples)
    if not snaps:
        return "No Strata snapshot was recorded at that time"
    s = min(snaps, key=lambda x: abs(x["t"] - (at or time.time())))
    if abs(s["t"] - (at or time.time())) > 30:
        return "The monitor was not running then"
    if not s.get("up"):
        return "Strata was not answering"
    lv = s.get("live") or {}
    bits = [lv.get("state") or "idle"]
    if lv.get("phase"):
        bits.append(str(lv["phase"]))
    if lv.get("elapsed_s"):
        bits.append(f"{lv['elapsed_s']:.0f} s into the request")
    if lv.get("generated"):
        bits.append(f"{lv['generated']:,} tokens written")
    if lv.get("queued"):
        bits.append(f"{lv['queued']} more request(s) waiting")
    return "Strata was " + ", ".join(bits)


def match_request(ts: float, kind: str) -> str:
    """The Strata request that belongs to a pi event at `ts`, in words ('' when nothing is known)."""
    with lock:
        reqs = list(req_log)
        live = ((state["metrics"] or {}).get("live") or {}) if state["up"] else {}
    if not reqs:
        return ""
    if kind == "length":
        near = [r for r in reqs if abs(r["end"] - ts) < 90]
        if not near:
            return ""
        r = min(near, key=lambda r: (r["finish"] != "length", abs(r["end"] - ts)))
        return (f" Matching Strata request: started {hhmm(r['start'])}, ran {r['end'] - r['start']:.0f} s, "
                f"{r['prompt']:,} prompt tokens, wrote {r['output']:,} at {r['tok_s']:.1f} tok/s, ended: {r['finish']}.")
    # timeout: did Strata have a request open at that moment?
    running = [r for r in reqs if r["start"] - 1 <= ts <= r["end"] + 1]
    if running:
        r = running[-1]
        return (f" Strata had a request open then: started {hhmm(r['start'])}, finished {hhmm(r['end'])} after "
                f"{r['end'] - r['start']:.0f} s with {r['output']:,} tokens (ended: {r['finish']}).")
    if live.get("state") not in (None, "idle") and live.get("elapsed_s") and time.time() - live["elapsed_s"] <= ts:
        return (f" Strata has a request open now ({live.get('elapsed_s', 0):.0f} s in, "
                f"{live.get('generated') or 0:,} tokens written).")
    before = [r for r in reqs if r["end"] <= ts]
    after = [r for r in reqs if r["start"] > ts]
    if before and (after or ts > max(r["end"] for r in reqs)):
        b = max(before, key=lambda r: r["end"])
        tail = f"; the next one started {hhmm(min(r['start'] for r in after))}" if after else ""
        return (f" Strata had no request open at that moment (the last one ended {ts - b['end']:.0f} s earlier"
                f"{tail}), so this request never reached Strata.")
    return ""


# ------------------------------------------------------------------ speed history
speed: dict = {}                    # model -> day -> {"tok": [...], "pre": [...]}
speed_warned: set = set()


def speed_load() -> bool:
    global speed
    speed = read_json(SPEED_FILE, None)
    if not isinstance(speed, dict):
        speed = {}
        return False
    return True


def speed_add(model: str, day: str, tok: float | None, pre: float | None, save: bool = True) -> None:
    with lock:
        d = speed.setdefault(model, {}).setdefault(day, {"tok": [], "pre": []})
        if tok:
            d["tok"] = (d["tok"] + [round(tok, 1)])[-1000:]
        if pre:
            d["pre"] = (d["pre"] + [round(pre)])[-300:]
    if save:
        with lock:
            snapshot = json.loads(json.dumps(speed))
        write_json(SPEED_FILE, snapshot)
        check_slowdown(model, day)


def speed_summary(days: int = 14) -> dict:
    out = {}
    with lock:
        data = json.loads(json.dumps(speed))
    for model, per_day in data.items():
        rows = []
        for day in sorted(per_day)[-days:]:
            d = per_day[day]
            rows.append({"day": day, "n": len(d["tok"]),
                         "tok": round(statistics.median(d["tok"]), 1) if d["tok"] else None,
                         "pre": round(statistics.median(d["pre"])) if d["pre"] else None})
        out[model] = rows
    return out


def check_slowdown(model: str, day: str) -> None:
    rows = {r["day"]: r for r in speed_summary(30).get(model, [])}
    today = rows.get(day)
    if not today or today["n"] < SLOWDOWN_MIN_REPLIES or (model, day) in speed_warned:
        return
    before = [r["tok"] for d, r in rows.items() if d < day and r["n"] >= SLOWDOWN_MIN_REPLIES and r["tok"]][-7:]
    if not before:
        return
    base = statistics.median(before)
    if today["tok"] < base * (1 - SLOWDOWN_PCT / 100):
        speed_warned.add((model, day))
        add_event("warn", "strata", "slowdown",
                  f"{model} writes at {today['tok']} tok/s today (median of {today['n']} replies), "
                  f"{100 - 100 * today['tok'] / base:.0f}% below its {base:.1f} tok/s of the previous days. "
                  f"Check the config changes, other programs on the GPU, and the GPU temperature.")


def speed_backfill_from_logs() -> int:
    """First run only: rebuild the history from Strata's own logs (dated by their 'engine started' lines)."""
    pat = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in ([\d.]+) ms \(([\d.]+) tok/s\), "
                     r"(\d+) generated in ([\d.]+) ms \(([\d.]+) tok/s\)")
    n = 0
    for path in glob.glob(os.path.join(STRATA_DIR, "strata-*.log")):
        cfg = read_json(path[:-4] + ".json", {}) or {}
        model = cfg.get("model_name") or os.path.basename(path)[7:-4]
        day = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\[strata\] (\d{4}-\d\d-\d\d) \d\d:\d\d:\d\d engine started", line)
                if m:
                    day = m.group(1)
                    continue
                m = pat.search(line)
                if m and day:
                    read, gen = int(m.group(3)), int(m.group(6))
                    speed_add(model, day, float(m.group(8)) if gen >= 100 else None,
                              float(m.group(5)) if read >= 2000 else None, save=False)
                    n += 1
    with lock:
        snapshot = json.loads(json.dumps(speed))
    write_json(SPEED_FILE, snapshot)
    return n


# ------------------------------------------------------------------ Strata /metrics
def poll_strata() -> None:
    seen_req: set = set()
    first = True
    last_prompt = None
    queued_since = None
    warned_long = False
    fails = 0
    flags: set = set()
    prev_elapsed = 0.0
    tool_since = None
    while True:
        t = time.time()
        try:
            hdr = {"Accept": "application/json"}
            if STRATA_API_KEY:
                hdr["Authorization"] = "Bearer " + STRATA_API_KEY
            req = urllib.request.Request(STRATA_URL + "/metrics", headers=hdr)
            with urllib.request.urlopen(req, timeout=10) as r:
                m = json.loads(r.read().decode("utf-8"))
            ok = True
        except Exception:
            m, ok = None, False
        # a busy Strata can answer its status page slowly: only call it down after 3 misses in a row
        fails = 0 if ok else fails + 1
        with lock:
            was_up = state["up"]
            up = ok or (was_up and fails < 3)
            state["up"] = up
            if ok:
                state["metrics"] = m
            if ok or not up:
                samples.append({"t": t, "up": up,
                                "live": (m or {}).get("live"), "hw": (m or {}).get("hardware")})
        if up != was_up and not first:
            add_event("info" if up else "warn", "strata", "server",
                      "Strata is answering again" if up else "Strata stopped answering on " + STRATA_URL)
        if up and m:
            ctx = (m.get("engine") or {}).get("max_context") or 0
            model = (m.get("engine") or {}).get("model") or "?"
            for rq in sorted(m.get("requests") or [], key=lambda x: x.get("time", 0)):
                key = rq.get("time")
                if key in seen_req:
                    continue
                seen_req.add(key)
                start = rq.get("time") or t                  # Strata's "time" is when the request started
                with lock:
                    req_log.append({"start": start, "end": start + (rq.get("duration_s") or 0), "model": model,
                                    "prompt": rq.get("prompt_tokens") or 0, "output": rq.get("output_tokens") or 0,
                                    "tok_s": rq.get("decode_tok_s") or 0, "finish": rq.get("finish") or "?"})
                if first:
                    last_prompt = rq.get("prompt_tokens")
                    continue
                check_request(rq, ctx, last_prompt, model)
                last_prompt = rq.get("prompt_tokens")
            lv = m.get("live") or {}
            q = lv.get("queued") or 0
            if q and queued_since is None:
                queued_since = t
            if not q:
                queued_since = None
            if queued_since and t - queued_since > 120 and not state.get("q_warned"):
                state["q_warned"] = True
                add_event("warn", "strata", "queue",
                          f"{q} request(s) have waited over 2 minutes behind the current one. "
                          f"pi gives up after 5 minutes of silence ({strata_doing(t)}).")
            if not queued_since:
                state["q_warned"] = False
            busy = lv.get("state") not in (None, "idle")
            if busy and lv.get("elapsed_s", 0) > 600 and not warned_long:
                warned_long = True
                add_event("warn", "strata", "long request",
                          f"One request has run for over 10 minutes ({strata_doing(t)}).")
            if not busy:
                warned_long = False
            # runaway replies: one reply still going past RUNAWAY_TOKENS, or one tool call taking very long
            el = lv.get("elapsed_s") or 0
            if not busy or el < prev_elapsed - 1:   # idle, or a new request started
                flags.clear()
                tool_since = None
            prev_elapsed = el
            gen = lv.get("generated") or 0
            if busy and gen > RUNAWAY_TOKENS and "runaway" not in flags:
                flags.add("runaway")
                add_event("warn", "strata", "runaway reply",
                          f"One reply has written {gen:,} tokens and is still going "
                          f"(limit {lv.get('max_tokens') or '?'}; {strata_doing(t)}). Very long replies are where "
                          f"loops and pi timeouts tend to happen.")
            phase = str(lv.get("phase") or "")
            if busy and phase.startswith("writing a tool call"):
                tool_since = tool_since or t
                if t - tool_since > TOOL_CALL_SLOW_S and "tool" not in flags:
                    flags.add("tool")
                    add_event("warn", "strata", "slow tool call",
                              f"The model has been writing one tool call for over {TOOL_CALL_SLOW_S // 60} minutes "
                              f"({strata_doing(t)}). A huge file write or a loop is likely.")
            else:
                tool_since = None
            check_hardware(m.get("hardware") or {}, t)
        first = False
        time.sleep(max(0.2, 1.0 - (time.time() - t)))


hw_runs: collections.Counter = collections.Counter()   # seconds each condition has lasted
hw_warned: set = set()


def check_hardware(hw: dict, t: float) -> None:
    """Warn when free VRAM, RAM or GPU temperature stays past its level for HW_SUSTAIN_S seconds. Each warning
    re-arms only after the value recovers clearly (so one bad stretch gives one line)."""
    mib = 1048576
    checks = []
    if hw.get("gpu_mem_total"):
        free = (hw["gpu_mem_total"] - (hw.get("gpu_mem_used") or 0)) / mib
        checks.append(("vram low", free < VRAM_LOW_MIB, free > VRAM_LOW_MIB + 150,
                       f"Only {free:.0f} MiB of graphics memory is free (of {hw['gpu_mem_total'] / mib / 1024:.1f} GB) "
                       f"for {HW_SUSTAIN_S} s. Strata's own log calls ~400 MiB little room on Windows; other programs "
                       f"using the GPU make it worse."))
    if hw.get("ram_total"):
        pct = 100 * (hw.get("ram_used") or 0) / hw["ram_total"]
        checks.append(("ram full", pct > RAM_FULL_PCT, pct < RAM_FULL_PCT - 3,
                       f"RAM is {pct:.0f}% full ({(hw.get('ram_used') or 0) / mib / 1024:.1f} of "
                       f"{hw['ram_total'] / mib / 1024:.1f} GB) for {HW_SUSTAIN_S} s. Windows pages to disk "
                       f"past this, which slows everything."))
    if hw.get("gpu_temp") is not None:
        c = hw["gpu_temp"]
        checks.append(("gpu hot", c >= GPU_HOT_C, c < GPU_HOT_C - 5,
                       f"The GPU has been at {c} °C or more for {HW_SUSTAIN_S} s."))
    for kind, bad, recovered, text in checks:
        hw_runs[kind] = hw_runs[kind] + 1 if bad else 0
        if hw_runs[kind] >= HW_SUSTAIN_S and kind not in hw_warned:
            hw_warned.add(kind)
            add_event("warn", "strata", kind, text, t)
        elif recovered:
            hw_warned.discard(kind)


def check_request(rq: dict, ctx: int, last_prompt, model: str) -> None:
    ts = (rq.get("time") or time.time()) + (rq.get("duration_s") or 0)   # logged when it ended
    p, out, fin = rq.get("prompt_tokens") or 0, rq.get("output_tokens") or 0, rq.get("finish") or "?"
    reused = rq.get("reused") or 0
    desc = (f"{p:,} prompt tokens ({reused:,} reused), {out:,} written in {rq.get('duration_s', 0):.0f} s, "
            f"{rq.get('decode_tok_s') or 0:.1f} tok/s, ended: {fin}")
    if fin == "length":
        add_event("warn", "strata", "reply cut at limit",
                  f"The reply stopped at the app's max_tokens limit ({out:,} tokens). {desc}", ts)
    elif fin in ("disconnect", "cancelled", "cancel"):
        add_event("warn", "strata", "app hung up",
                  f"The app closed the connection before the reply finished. {desc}", ts)
    elif fin not in ("stop", "tool_calls", "tool_use", "end_turn"):
        add_event("warn", "strata", "unusual ending", desc, ts)
    else:
        add_event("info", "strata", "request", desc, ts)
    if ctx and p + out > 0.85 * ctx:
        add_event("warn", "strata", "near context limit",
                  f"This conversation used {p + out:,} of {ctx:,} tokens ({100 * (p + out) / ctx:.0f}%).", ts)
    if last_prompt and last_prompt > 20000 and p < 0.6 * last_prompt and reused < 0.5 * p:
        add_event("warn", "strata", "conversation shortened",
                  f"The prompt dropped from {last_prompt:,} to {p:,} tokens: the app compacted or restarted "
                  f"the conversation.", ts)
        if fin == "length":
            # pi's summary request: the older part of the chat (nothing reused) ran into the summary's token cap.
            # pi writes nothing to its session file when that happens, so this is the only place it shows.
            add_event("warn", "pi", "compaction failed",
                      f"pi tried to compact ({last_prompt:,} tokens) but the summary hit its {out:,}-token cap and "
                      f"was cut off, so nothing was saved and pi will try again. If this repeats, the summary has "
                      f"outgrown its cap: start a new pi session, lower keepRecentTokens, or use a short-summary "
                      f"extension (D:\\strata\\Handoff.md, 3.7). Raising reserveTokens moves compaction earlier and "
                      f"can make a loop worse.", ts)
    read, pms = rq.get("prompt_read") or 0, rq.get("prompt_ms") or 0
    speed_add(model, dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
              rq.get("decode_tok_s") if out >= 100 else None,
              read / (pms / 1000) if read >= 2000 and pms else None)


# ------------------------------------------------------------------ file tailing
class Tail:
    def __init__(self, path: str, from_start: bool = False):
        self.path = path
        self.pos = 0 if from_start else os.path.getsize(path)
        self.buf = b""

    def lines(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.pos:
            self.pos = 0
        if size == self.pos:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read(size - self.pos)
        self.pos = size
        data = self.buf + data
        parts = data.split(b"\n")
        self.buf = parts.pop()
        return [x.decode("utf-8", "replace").rstrip("\r") for x in parts]


def pi_event(e: dict, write: bool, prev_ts: float | None = None) -> None:
    """One line of a pi session file. `prev_ts` is the time of the message before it in the same session (for a
    failed request: roughly when pi sent it)."""
    ts_s = e.get("timestamp") or ""
    try:
        ts = dt.datetime.fromisoformat(ts_s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        ts = time.time()
    if e.get("type") == "compaction":
        add_event("warn", "pi", "pi compacted",
                  f"pi summarised the conversation at {e.get('tokensBefore', 0):,} tokens and kept only the most "
                  f"recent part word for word.", ts, write)
        return
    m = e.get("message")
    if not isinstance(m, dict) or m.get("provider") != "local-llm":
        return
    why, model, err = m.get("stopReason"), m.get("model", "?"), (m.get("errorMessage") or "").strip()
    # a live event waits a few seconds, so the matching Strata request has reached /metrics before it is linked
    delay = 8.0 if write else 0.0
    if why == "error":
        if "timed out" in err.lower():
            since = ts - prev_ts if prev_ts else None

            def emit():
                when = (f" {since:.0f} s after it sent the request" if since is not None else "")
                quick = (" It failed almost at once, so pi did not wait 5 minutes: the request failed on pi's side "
                         "before Strata answered." if since is not None and since < 15 else "")
                doing = strata_doing(ts)
                ctx = f" At that moment: {doing}." if doing.startswith("Strata") else ""
                add_event("error", "pi", "timeout",
                          f"pi reported 'Request timed out.' ({model}){when}.{quick}{ctx}"
                          f"{match_request(ts, 'timeout')} pi usually retries by itself.", ts, write)
            later(delay, emit) if write else emit()
        elif err.startswith("400") and "context" in err:
            add_event("error", "pi", "context too long",
                      f"Strata refused the request ({model}): {err[:300]}", ts, write)
        else:
            add_event("error", "pi", "error", f"{model}: {err[:300]}", ts, write)
    elif why == "length":
        th = tx = tc = 0
        for c in m.get("content") or []:
            if c.get("type") == "thinking":
                th += len(c.get("thinking") or "")
            elif c.get("type") == "text":
                tx += len(c.get("text") or "")
            elif c.get("type") == "toolCall":
                tc += len(json.dumps(c.get("arguments") or ""))
        out = (m.get("usage") or {}).get("output")
        what = ("all thinking, no answer: the model used the whole budget reasoning" if th and not tx and not tc
                else f"mostly a tool call ({tc:,} characters of arguments)" if tc > max(th, tx)
                else f"thinking {th:,} characters, answer {tx:,} characters")

        def emit_len():
            add_event("warn", "pi", "reply cut at limit",
                      f"{model}: pi showed 'Response was truncated before completion.' The reply reached pi's "
                      f"maxTokens{f' ({out:,} tokens)' if out else ''}; it was {what}.{match_request(ts, 'length')}",
                      ts, write)
        later(delay, emit_len) if write else emit_len()
    elif why == "aborted":
        add_event("info", "pi", "aborted", f"{model}: the request was stopped ({err or 'aborted'}).", ts, write)


def watch_files() -> None:
    tails: dict = {}
    last_ts: dict = {}              # pi session file -> time of its latest message
    # backfill recent pi history once (shown on the page, not written to the log again)
    cutoff = time.time() - BACKFILL_HOURS * 3600
    for f in glob.glob(os.path.join(PI_SESSIONS, "*", "*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                tails[f] = Tail(f)
                continue
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                        ets = dt.datetime.fromisoformat((e.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
                    if ets >= cutoff:
                        pi_event(e, write=False, prev_ts=last_ts.get(f))
                    if e.get("message"):
                        last_ts[f] = ets
            tails[f] = Tail(f)
        except OSError:
            pass
    for f in glob.glob(os.path.join(STRATA_DIR, "strata-*.log")):
        tails[f] = Tail(f)
    with lock:
        events_sorted = sorted(events, key=lambda x: x["ts"])
        events.clear()
        events.extend(events_sorted)
    add_event("info", "monitor", "started",
              f"Strata Live {VERSION}: watching {STRATA_URL}, {len([t for t in tails if t.endswith('.log')])} Strata "
              f"log(s) and {len([t for t in tails if t.endswith('.jsonl')])} pi session file(s). Logs go to {LOG_DIR}."
              + (f" Relay on 127.0.0.1:{RELAY_PORT}." if RELAY_PORT else ""))
    while True:
        for f in glob.glob(os.path.join(PI_SESSIONS, "*", "*.jsonl")) + glob.glob(os.path.join(STRATA_DIR, "strata-*.log")):
            if f not in tails:
                tails[f] = Tail(f, from_start=f.endswith(".jsonl"))
        for f, t in list(tails.items()):
            for line in t.lines():
                if f.endswith(".jsonl"):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    pi_event(e, write=True, prev_ts=last_ts.get(f))
                    if e.get("message"):
                        try:
                            last_ts[f] = dt.datetime.fromisoformat(
                                (e.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
                        except ValueError:
                            pass
                else:
                    strata_line(os.path.basename(f), line)
        time.sleep(1.0)


def strata_line(name: str, line: str) -> None:
    low = line.lower()
    if "engine started" in low:
        model = name.replace("strata-", "").replace(".log", "")
        add_event("info", "strata", "engine started", f"Strata started the {model} model.")
    elif "(cancelled)" in low:
        add_event("info", "strata", "cancelled", line.replace("strata serve: ", "")[:300])
    elif "warning" in low:
        add_event("warn", "strata", "warning", line[:300])
    elif ("error" in low and "no error" not in low and "error 1314" not in low) or "fatal" in low or "exceeds the context" in low:
        add_event("error", "strata", "log error", line[:300])


# ------------------------------------------------------------------ settings changes
# real secrets only: api_key / apiKey / --api-key, secret, password, authorization, a key named "token"
# (not reasoning_budget_tokens or max_tokens, which are counts)
SECRET = re.compile(r"api[_-]?key|secret|passw|authoriz|bearer|(^|[_-])(auth|token|access[_-]?key)$", re.I)


def redact(obj, key: str = ""):
    if isinstance(obj, dict):
        return {k: ("***" if SECRET.search(k) and not isinstance(v, (dict, list)) else redact(v, k)) for k, v in obj.items()}
    if isinstance(obj, list):
        out, hide = [], False
        for v in obj:
            out.append("***" if hide else redact(v, key))
            hide = isinstance(v, str) and v.startswith("--") and bool(SECRET.search(v))
        return out
    return obj


def flatten(obj, prefix: str = "") -> dict:
    """Settings as path -> value. An engine `args` list becomes one entry per --flag, so a change reads
    '--pcie-frac 0.35 -> 0.55' instead of 'args[23] changed'."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list) and prefix.split(".")[-1] == "args" and all(isinstance(x, str) for x in obj):
        flag, vals = None, []
        for tok in obj + ["--"]:
            if tok.startswith("--"):
                if flag:
                    out[f"{prefix} {flag}"] = " ".join(vals) if vals else "(on)"
                flag, vals = tok, []
            else:
                vals.append(tok)
    else:
        out[prefix] = json.dumps(obj, ensure_ascii=False) if isinstance(obj, (list, dict)) else obj
    return out


def tracked_configs() -> list:
    paths = glob.glob(os.path.join(STRATA_DIR, "strata-*.json"))
    paths += [os.path.join(STRATA_DIR, "engine", "BUILD.json"),
              os.path.join(PI_AGENT, "models.json"), os.path.join(PI_AGENT, "settings.json")]
    return [p for p in paths if os.path.isfile(p)]


def config_snapshot(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    try:
        return {"json": redact(json.loads(text))}
    except ValueError:
        return {"sha256": hashlib.sha256(text.encode()).hexdigest()}


def describe_change(old, new) -> str:
    if old is None:
        return "new file"
    if new is None:
        return "removed"
    if "json" not in old or "json" not in new:
        return "content changed"
    a, b = flatten(old["json"]), flatten(new["json"])
    parts = []
    for k in sorted(set(a) | set(b)):
        if k not in a:
            parts.append(f"added {k} = {b[k]}")
        elif k not in b:
            parts.append(f"removed {k} (was {a[k]})")
        elif a[k] != b[k]:
            parts.append(f"{k}: {a[k]} -> {b[k]}")
    return "; ".join(parts) if parts else "saved again with no change"


def watch_configs() -> None:
    saved = read_json(CONFIG_STATE_FILE, None)
    first_run = not isinstance(saved, dict)
    saved = saved if isinstance(saved, dict) else {}
    mtimes: dict = {}

    def scan(off: bool) -> None:
        changed = False
        current = set(tracked_configs())
        for path in current | set(saved):
            try:
                mt = os.path.getmtime(path)
            except OSError:
                mt = None
            if mtimes.get(path, "unseen") == mt:
                continue
            mtimes[path] = mt
            snap = config_snapshot(path) if mt else None
            if snap != saved.get(path):
                if not first_run or off is False:
                    what = describe_change(saved.get(path), snap)
                    name = path.replace(os.path.expanduser("~"), "~")
                    add_event("info", "config", "config changed",
                              f"{name}: {what}" + (" (changed while the monitor was off)" if off else ""))
                if snap is None:
                    saved.pop(path, None)
                else:
                    saved[path] = snap
                changed = True
        if changed:
            write_json(CONFIG_STATE_FILE, saved)

    scan(off=True)
    if first_run:
        add_event("info", "config", "config tracking",
                  f"Now tracking {len(saved)} settings file(s) for changes: Strata's strata-*.json and BUILD.json, "
                  f"pi's models.json and settings.json. Secrets are stored as ***.")
        first_run = False
    while True:
        time.sleep(10)
        scan(off=False)


# ------------------------------------------------------------------ Windows events
WINEV_QUERY = ("*[System[(EventID=4101 or EventID=2004 or EventID=41 or EventID=6008 or "
               "Provider[@Name='Microsoft-Windows-WHEA-Logger'] or Provider[@Name='nvlddmkm'] or "
               "Provider[@Name='amdkmdag']) and TimeCreated[timediff(@SystemTime) <= {ms}]]]")
NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def classify_winev(provider: str, eid: int) -> tuple:
    if eid == 4101:
        return "error", "graphics driver reset", "Windows reset the graphics driver after it stopped responding (TDR). Any running Strata request fails."
    if provider in ("nvlddmkm", "amdkmdag"):
        return "error", "graphics driver error", "The graphics driver reported an error."
    if "WHEA" in provider:
        return "error", "hardware error", "Windows logged a hardware error (WHEA): CPU, memory or PCIe."
    if eid == 2004:
        return "warn", "windows low memory", "Windows ran low on memory and named the programs using the most."
    if eid in (41, 6008):
        return "error", "unexpected shutdown", "The PC shut down or restarted without a clean shutdown (crash, power loss or reset)."
    return "warn", "windows event", "A watched Windows event."


def query_winev(hours: float) -> list | None:
    try:
        flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
        out = subprocess.run(["wevtutil", "qe", "System", "/q:" + WINEV_QUERY.format(ms=int(hours * 3600000)),
                              "/f:RenderedXml", "/rd:true", "/c:100", "/e:Events"],
                             capture_output=True, timeout=30, creationflags=flags)
        root = ET.fromstring(out.stdout.decode("utf-8", "replace") or "<Events/>")
    except (OSError, subprocess.SubprocessError, ET.ParseError):
        return None
    found = []
    for ev in root.findall(f"{NS}Event"):
        sysn = ev.find(f"{NS}System")
        if sysn is None:
            continue
        prov = (sysn.find(f"{NS}Provider").get("Name") if sysn.find(f"{NS}Provider") is not None else "?")
        try:
            eid = int((sysn.findtext(f"{NS}EventID") or "0").strip())
            rid = int((sysn.findtext(f"{NS}EventRecordID") or "0").strip())
            ts = dt.datetime.fromisoformat(sysn.find(f"{NS}TimeCreated").get("SystemTime")[:26].rstrip("Z")
                                           ).replace(tzinfo=dt.timezone.utc).timestamp()
        except (ValueError, AttributeError):
            continue
        msg = ev.findtext(f"{NS}RenderingInfo/{NS}Message") or ""
        msg = " ".join("".join(c for c in msg if unicodedata.category(c) != "Cf").split())   # drop invisible marks
        msg = re.sub(r"\?(?=\d)", "", msg)       # wevtutil prints those marks in dates as "?" ("?10/?9/?2026")
        found.append({"id": rid, "ts": ts, "provider": prov, "event_id": eid, "message": msg[:400]})
    return sorted(found, key=lambda x: x["ts"])


def watch_windows() -> None:
    if os.name != "nt":
        state["winev"] = "not Windows"
        return
    seen = set(read_json(WINEV_FILE, {}).get("seen", []) if isinstance(read_json(WINEV_FILE, {}), dict) else [])
    first = True
    while True:
        found = query_winev(BACKFILL_HOURS if first else 2)
        if found is None:
            state["winev"] = "cannot read the System event log"
        else:
            state["winev"] = "ok"
            for e in found:
                level, kind, plain = classify_winev(e["provider"], e["event_id"])
                is_new = e["id"] not in seen
                with lock:
                    if not any(w["id"] == e["id"] for w in winev_seen):
                        winev_seen.append({**e, "kind": kind})
                if first and not is_new:
                    add_event(level, "windows", kind, f"{plain} ({e['provider']} event {e['event_id']}): {e['message']}",
                              e["ts"], write=False)
                elif is_new:
                    add_event(level, "windows", kind, f"{plain} ({e['provider']} event {e['event_id']}): {e['message']}",
                              e["ts"], write=True)
                seen.add(e["id"])
            write_json(WINEV_FILE, {"seen": sorted(seen)[-2000:]})
        first = False
        time.sleep(60)


# ------------------------------------------------------------------ housekeeping
def cleanup() -> None:
    while True:
        now = time.time()
        removed = 0
        for pattern, keep in ((os.path.join(LOG_DIR, "events-*.log"), LOG_KEEP_DAYS),
                              (os.path.join(LOG_DIR, "relay-*.log"), LOG_KEEP_DAYS),
                              (os.path.join(INCIDENT_DIR, "*.json"), LOG_KEEP_DAYS),
                              (os.path.join(BUNDLE_DIR, "*.zip"), BUNDLE_KEEP_DAYS)):
            for p in glob.glob(pattern):
                try:
                    if now - os.path.getmtime(p) > keep * 86400:
                        os.remove(p)
                        removed += 1
                except OSError:
                    pass
        jl = os.path.join(LOG_DIR, "events.jsonl")
        try:
            if os.path.getsize(jl) > JSONL_MAX_MB * 1048576:
                with open(jl, "rb") as f:
                    f.seek(-JSONL_MAX_MB * 1048576 // 2, os.SEEK_END)
                    f.readline()
                    tail = f.read()
                with open(jl + ".tmp", "wb") as f:
                    f.write(tail)
                os.replace(jl + ".tmp", jl)
                removed += 1
        except OSError:
            pass
        if removed:
            load_incidents()
            add_event("info", "monitor", "cleanup",
                      f"Removed {removed} old log file(s) (older than {LOG_KEEP_DAYS} days; bundles after "
                      f"{BUNDLE_KEEP_DAYS} days) or trimmed events.jsonl.")
        time.sleep(6 * 3600)


# ------------------------------------------------------------------ diagnostics bundle
def make_bundle() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"strata-live-bundle-{stamp}.zip"
    path = os.path.join(BUNDLE_DIR, name)
    with lock:
        m = json.loads(json.dumps(state["metrics"] or {}))
        counts = dict(state["counts"])
        ev = list(events)
        incs = sorted(state["incidents"], key=lambda i: i["ts"])[-10:]
        win = list(winev_seen)
        relays = list(relay_log)
    eng, hs = m.get("engine") or {}, m.get("hardware_static") or {}
    lines = [
        f"Strata Live diagnostics bundle, made {local(time.time())}",
        f"Strata Live {VERSION}; Python {platform.python_version()}; {platform.platform()}",
        f"Strata: {STRATA_URL} ({'answering' if state['up'] else 'NOT answering'}); folder {STRATA_DIR}",
        f"Model {eng.get('model')}; engine {eng.get('version')}; context {eng.get('max_context')}; "
        f"experts on GPU {eng.get('expert_slots')}; VRAM free at load {eng.get('vram_free_mib')} MiB; "
        f"projection {eng.get('cvec') or 'off'}",
        f"Hardware: {hs.get('gpu_name')} x{hs.get('gpu_count')}; {hs.get('cpu_name')} "
        f"({hs.get('cores')} cores / {hs.get('threads')} threads)",
        "",
        "Event counts since the monitor started:",
        *[f"  {k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])],
        "",
        "Contents: metrics-now.json (Strata's status page), events-recent.jsonl, events-<date>.log (last 3 days),",
        "incidents/ (last 10 recordings), configs/ (secrets replaced by ***), speed-history.json,",
        "windows-events.json, relay.json, strata-logs/ (last 400 lines of each Strata log).",
        "Not included: pi's session files, which hold your conversations.",
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "\n".join(lines) + "\n")
        z.writestr("metrics-now.json", json.dumps(m, indent=1))
        z.writestr("events-recent.jsonl", "\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n")
        for p in sorted(glob.glob(os.path.join(LOG_DIR, "events-*.log")))[-3:]:
            z.write(p, os.path.basename(p))
        for i in incs:
            p = os.path.join(INCIDENT_DIR, i["name"] + ".json")
            if os.path.isfile(p):
                z.write(p, "incidents/" + os.path.basename(p))
        for p in tracked_configs():
            snap = config_snapshot(p)
            body = json.dumps(snap.get("json"), indent=1, ensure_ascii=False) if snap and "json" in snap else "(not JSON)"
            label = ("pi-" if p.startswith(PI_AGENT) else "") + os.path.basename(p)
            z.writestr("configs/" + label, body)
        z.writestr("speed-history.json", json.dumps(speed_summary(30), indent=1))
        z.writestr("windows-events.json", json.dumps(win, indent=1, ensure_ascii=False))
        z.writestr("relay.json", json.dumps(relays, indent=1))
        for p in glob.glob(os.path.join(STRATA_DIR, "strata-*.log")):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    tail = collections.deque(f, maxlen=400)
                z.writestr("strata-logs/" + os.path.basename(p), "".join(tail))
            except OSError:
                pass
    add_event("info", "monitor", "bundle made", f"Diagnostics bundle saved to {path}")
    return name


# ------------------------------------------------------------------ optional relay (app -> relay -> Strata)
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
       "transfer-encoding", "upgrade", "host", "content-length"}
relay_ids = iter(range(1, 10 ** 9))


class RelayHandler(BaseHTTPRequestHandler):
    """Passes each request to Strata unchanged and streams the answer back as it arrives, timing: when the request
    came in, when Strata answered, when the first data arrived, the longest silence, and how it ended."""
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.relay()

    def do_POST(self):
        self.relay()

    def do_DELETE(self):
        self.relay()

    def do_OPTIONS(self):
        self.relay()

    def relay(self):
        rid = next(relay_ids)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        info = {}
        if body[:1] == b"{":
            try:
                j = json.loads(body)
                info = {"model": j.get("model"), "stream": j.get("stream"),
                        "max_tokens": j.get("max_tokens") or j.get("max_completion_tokens"),
                        "messages": len(j.get("messages") or [])}
            except ValueError:
                pass
        rec = {"id": rid, "start": time.time(), "method": self.command, "path": self.path.split("?")[0],
               "req_bytes": len(body), **info, "status": None, "headers_s": None, "first_byte_s": None,
               "last_byte": None, "max_gap_s": 0.0, "bytes": 0, "end": None, "ended": "running", "warned": 0}
        with lock:
            relay_active[rid] = rec
        u = urllib.parse.urlsplit(STRATA_URL)
        try:
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=3600)
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            hdrs["Content-Length"] = str(len(body))
            conn.request(self.command, self.path, body=body or None, headers=hdrs)
            resp = conn.getresponse()
        except Exception as e:
            rec.update(ended=f"Strata unreachable: {e}"[:200], end=time.time())
            self.finish_rec(rec)
            try:
                self.send_error(502, "Strata Live relay: Strata is not answering")
            except OSError:
                pass
            return
        rec["status"], rec["headers_s"] = resp.status, round(time.time() - rec["start"], 2)
        try:
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP or k.lower() == "content-length":
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
        except OSError:
            rec.update(ended="app hung up", end=time.time())
            conn.close()
            self.finish_rec(rec)
            return
        ended = "done"
        while True:
            try:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)
            except Exception as e:
                ended = f"Strata connection error: {e}"[:200]
                break
            if not chunk:
                break
            now = time.time()
            if rec["first_byte_s"] is None:
                rec["first_byte_s"] = round(now - rec["start"], 2)
            else:
                rec["max_gap_s"] = round(max(rec["max_gap_s"], now - rec["last_byte"]), 1)
            rec["last_byte"] = now
            rec["bytes"] += len(chunk)
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except OSError:
                ended = "app hung up"
                break
        conn.close()
        rec.update(ended=ended, end=time.time())
        self.finish_rec(rec)

    @staticmethod
    def finish_rec(rec: dict) -> None:
        with lock:
            relay_active.pop(rec["id"], None)
            relay_log.append(rec)
        dur = rec["end"] - rec["start"]
        line = (f"{local(rec['start'])}  #{rec['id']} {rec['method']} {rec['path']} model={rec.get('model')} "
                f"status={rec['status']} headers={rec['headers_s']}s first_data={rec['first_byte_s']}s "
                f"longest_silence={rec['max_gap_s']}s took={dur:.1f}s bytes={rec['bytes']} ended={rec['ended']}")
        day = dt.datetime.fromtimestamp(rec["start"]).strftime("%Y-%m-%d")
        with open(os.path.join(LOG_DIR, f"relay-{day}.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if not rec["path"].startswith("/v1/") and not rec["path"].startswith("/api/"):
            return
        bad = rec["ended"] != "done" or (rec["status"] or 0) >= 400
        add_event("warn" if bad else "info", "relay", "relay request",
                  f"{rec['method']} {rec['path']}: status {rec['status']}, Strata answered after {rec['headers_s']} s, "
                  f"first data after {rec['first_byte_s']} s, longest silence {rec['max_gap_s']} s, took {dur:.0f} s, "
                  f"ended: {rec['ended']}.")


def relay_watchdog() -> None:
    """Warns while a relayed request gets no data from Strata (the situation that makes pi time out)."""
    while True:
        time.sleep(5)
        now = time.time()
        with lock:
            active = list(relay_active.values())
        for rec in active:
            quiet = now - (rec["last_byte"] or rec["start"])
            for i, limit in enumerate(RELAY_SILENCE_WARN_S):
                if quiet >= limit and rec["warned"] <= i:
                    rec["warned"] = i + 1
                    stage = "before any data" if rec["first_byte_s"] is None else "in the middle of the answer"
                    add_event("warn", "relay", "relay silence",
                              f"Request #{rec['id']} ({rec['path']}) has had no data from Strata for {quiet:.0f} s, "
                              f"{stage}. pi gives up at 300 s. {strata_doing(now)}.")


# ------------------------------------------------------------------ web page
BUNDLE_NAME = re.compile(r"^strata-live-bundle-\d{8}-\d{6}\.zip$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _local_page(self) -> bool:
        """Only this page may ask for a bundle: a web site cannot reach it through the browser (DNS rebinding)."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin") or ""
        return host in ("127.0.0.1", "localhost") and (not origin or re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin))

    def do_POST(self):
        if self.path == "/api/bundle" and self._local_page():
            try:
                name = make_bundle()
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
            return self._send(200, json.dumps({"name": name, "path": os.path.join(BUNDLE_DIR, name)}).encode(),
                              "application/json")
        self._send(404, b"not found", "text/plain")

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                  # the browser went away mid-reply
        except Exception as e:                    # report it instead of an empty reply
            print(f"Strata Live page error on {self.path}: {e!r}", flush=True)
            try:
                self._send(500, json.dumps({"error": repr(e)}).encode(), "application/json")
            except OSError:
                pass

    def get(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "live.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path.startswith("/api/state"):
            with lock:
                m = state["metrics"] or {}
                ctx = (m.get("engine") or {}).get("max_context") or 0
                body = {
                    "version": VERSION, "up": state["up"], "now": time.time(), "since": state["since"],
                    "engine": m.get("engine"), "live": m.get("live"), "hardware": m.get("hardware"),
                    "hardware_static": m.get("hardware_static"), "totals": m.get("totals"),
                    "requests": (m.get("requests") or [])[:12],
                    "pi_compacts_at": pi_compacts_at(ctx, (m.get("engine") or {}).get("model")) if ctx else None,
                    "samples": [{"t": s["t"], "tok_s": ((s.get("live") or {}).get("tok_s") or 0) if (s.get("live") or {}).get("state") == "generating" else 0,
                                 "gpu": (s.get("hw") or {}).get("gpu_util"), "rx": (s.get("hw") or {}).get("gpu_pcie_rx_mb")}
                                for s in list(samples)[-120:]],
                    "events": list(events)[-250:], "counts": dict(state["counts"]), "log_dir": LOG_DIR,
                    "incidents": state["incidents"][-50:],
                    "recording": state["open_incident"]["name"] if state["open_incident"] else None,
                    "relay": {"port": RELAY_PORT, "recent": [dict(r) for r in list(relay_log)[-15:]],
                              "active": [dict(r) for r in relay_active.values()]},
                    "windows": state["winev"],
                }
            body["speed"] = speed_summary(14)
            return self._send(200, json.dumps(body, default=str).encode(), "application/json")
        if self.path.startswith("/api/incident?name="):
            name = urllib.parse.unquote(self.path.split("=", 1)[1])
            with lock:
                known = {i["name"] for i in state["incidents"]}
            if name not in known:                 # only names from the index: no paths from the URL
                return self._send(404, b"no such incident", "text/plain")
            with open(os.path.join(INCIDENT_DIR, name + ".json"), "rb") as f:
                return self._send(200, f.read(), "application/json")
        if self.path.startswith("/api/bundle/"):
            name = urllib.parse.unquote(self.path.rsplit("/", 1)[1])
            p = os.path.join(BUNDLE_DIR, name)
            if not BUNDLE_NAME.match(name) or not os.path.isfile(p):
                return self._send(404, b"no such bundle", "text/plain")
            with open(p, "rb") as f:
                return self._send(200, f.read(), "application/zip",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
        self._send(404, b"not found", "text/plain")


class Server(ThreadingHTTPServer):
    allow_reuse_address = False      # on Windows, reuse would let a second copy share the port silently
    daemon_threads = True


def main() -> None:
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Strata Live is already running (port {PORT} is taken): open http://127.0.0.1:{PORT}/", flush=True)
        if "--no-browser" not in sys.argv:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return
    relay_srv = None
    if RELAY_PORT:
        try:
            relay_srv = Server(("127.0.0.1", RELAY_PORT), RelayHandler)
        except OSError:
            print(f"The relay port {RELAY_PORT} is taken; starting without the relay.", flush=True)
    load_incidents()
    if not speed_load():
        n = speed_backfill_from_logs()
        print(f"Speed history rebuilt from Strata's logs ({n} requests).", flush=True)
    for target in (poll_strata, watch_files, watch_configs, watch_windows, cleanup):
        threading.Thread(target=target, daemon=True).start()
    if relay_srv:
        threading.Thread(target=relay_srv.serve_forever, daemon=True).start()
        threading.Thread(target=relay_watchdog, daemon=True).start()
    print(f"Strata Live {VERSION}: http://127.0.0.1:{PORT}/   (logs in {LOG_DIR}; Ctrl+C to stop)", flush=True)
    if relay_srv:
        print(f"Relay: point your app at http://127.0.0.1:{RELAY_PORT}/v1 instead of {STRATA_URL}/v1", flush=True)
    if "--no-browser" not in sys.argv:
        try:
            import webbrowser
            later(1.0, webbrowser.open, f"http://127.0.0.1:{PORT}/")
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
