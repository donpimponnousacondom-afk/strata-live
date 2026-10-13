"""Engine control for Strata Monitor's console: tuning, load/unload, restart and shutdown, and speed trials.

Everything here runs only when the user clicks a control on the panel; nothing is called on a timer. Each change is
written to logs\\tuning.jsonl and to the ship's log. It talks to Strata on 127.0.0.1 through Strata's own API:

  GET/POST /settings   the shared settings: defaults for what a request leaves out (temperature, top_p, top_k,
                       reasoning_effort, reasoning_budget_tokens, max_tokens). Used from the next request.
  GET/POST /config     the run config's documented keys (Strata checks every value). Used from the next start.
                       Strata keeps one <config>.bak; this module also copies the config to
                       backups\\config\\<time>-<name> first, so earlier copies are never lost.
  POST /unload, /load  free the GPU / load the model again, with the server still up.

A full restart or shutdown stops the server's processes (run-<model>.bat -> server.py -> strata.exe) after an unload,
and a start runs the same run-<model>.bat again, in a minimized window.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

import strata_live as sl

HERE = os.path.dirname(os.path.abspath(__file__))
TUNING_LOG = os.path.join(sl.LOG_DIR, "tuning.jsonl")
TRIALS_LOG = os.path.join(sl.LOG_DIR, "speed-trials.jsonl")
DIAG_LOG = os.path.join(sl.LOG_DIR, "diagnostics.jsonl")
CONFIG_BACKUPS = os.path.join(HERE, "backups", "config")
STATE_FILE = os.path.join(sl.LOG_DIR, "control-state.json")
NO_WINDOW = 0x08000000

# the live tuning the panel offers: key -> (label, lo, hi, step, default when nothing is set)
LIVE_KEYS = {
    "temperature": ("TEMPERATURE", 0.0, 2.0, 0.05, None),
    "top_p": ("TOP P", 0.05, 1.0, 0.05, None),
    "top_k": ("TOP K", 1, 64, 1, None),
    "reasoning_budget_tokens": ("THINKING BUDGET", 0, 32768, 512, 0),
    "max_tokens": ("DEFAULT REPLY LIMIT", 0, 32768, 1024, 0),
}
EFFORTS = ("none", "low", "medium", "high")
# saved settings (from the next full restart): key -> (label, lo, hi, step)
SAVED_KEYS = {
    "vram_reserve_mib": ("VRAM RESERVE MiB", 0, 2000, 50),
    "idle_unload_s": ("IDLE UNLOAD S", 0, 3600, 60),
    "engine_silence_s": ("ENGINE SILENCE LIMIT S", 0, 900, 30),
    "sampling.temperature": ("CONFIG TEMPERATURE", 0.0, 2.0, 0.05),
    "sampling.top_p": ("CONFIG TOP P", 0.05, 1.0, 0.05),
    "sampling.top_k": ("CONFIG TOP K", 1, 64, 1),
}

_lock = threading.Lock()
action = {"name": None, "since": 0.0, "note": ""}      # the control running now (one at a time)
last_result = {"text": "", "ts": 0.0, "ok": True}


# ---------------------------------------------------------------- HTTP
def _url(path: str) -> str:
    return sl.STRATA_URL.rstrip("/") + path


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    key = os.environ.get("STRATA_API_KEY")
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def http(method: str, path: str, body=None, timeout: float = 10.0):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(_url(path), data=data, method=method, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def metrics() -> dict:
    with sl.lock:
        return dict(sl.state.get("metrics") or {})


def busy() -> str | None:
    """Why Strata can't be interrupted now, or None."""
    m = metrics()
    live = m.get("live") or {}
    if not m:
        return None
    if live.get("state") in ("reading", "generating"):
        return f"Strata is {live['state']} a request"
    if live.get("queued"):
        return f"{live['queued']} request(s) waiting"
    return None


# ---------------------------------------------------------------- the record of changes
def note(kind: str, text: str, ok: bool = True, **extra) -> None:
    rec = {"ts": time.time(), "kind": kind, "ok": ok, "text": text, **extra}
    try:
        with open(TUNING_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    sl.add_event("info" if ok else "warn", "control", kind, text)
    last_result.update(text=text, ts=rec["ts"], ok=ok)


def state() -> dict:
    return sl.read_json(STATE_FILE, {}) or {}


def save_state(**kw) -> None:
    s = state()
    s.update(kw)
    sl.write_json(STATE_FILE, s)


def run_action(name: str, fn, *args) -> bool:
    """Run one control in the background; False if another one is still running."""
    with _lock:
        if action["name"]:
            return False
        action.update(name=name, since=time.time(), note="")

    def work():
        try:
            fn(*args)
        except Exception as e:                                   # shown on the panel and in the ship's log
            note(name, f"{name} failed: {e}", ok=False)
        finally:
            with _lock:
                action.update(name=None, note="")
    threading.Thread(target=work, daemon=True).start()
    return True


# ---------------------------------------------------------------- live tuning (POST /settings)
def get_shared() -> dict:
    code, body = http("GET", "/settings", timeout=4)
    return (body.get("defaults") or {}) if code == 200 else {}


def set_live(key: str, value) -> None:
    """Set one shared setting (None clears it); the others stay as they are."""
    cur = get_shared()
    old = cur.get(key)
    new = dict(cur)
    if value is None or (key == "max_tokens" and value == 0):          # 0 on the reply-limit slider: no default
        new.pop(key, None)
    else:
        new[key] = value
    code, body = http("POST", "/settings", {"defaults": new or None})
    if code != 200:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {code}")
    note("live tuning", f"{key}: {old if old is not None else 'default'} -> {value if value is not None else 'default'}"
                        " (from the next request)", key=key, old=old, new=value)


def clear_live() -> None:
    cur = get_shared()
    code, body = http("POST", "/settings", {"defaults": None})
    if code != 200:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {code}")
    note("live tuning", "all live tuning cleared: Strata uses its config again" +
         (f" (was {', '.join(f'{k}={v}' for k, v in cur.items())})" if cur else ""), old=cur, new={})


# ---------------------------------------------------------------- saved settings (POST /config)
def get_config() -> dict:
    code, body = http("GET", "/config", timeout=4)
    if code != 200:
        return {}
    return {k["key"]: k.get("value") for k in body.get("keys") or []} | {"_file": body.get("file")}


def config_path() -> str | None:
    name = get_config().get("_file")                    # the server names its own run config
    if name and os.path.isfile(os.path.join(sl.STRATA_DIR, name)):
        return os.path.join(sl.STRATA_DIR, name)
    model = (metrics().get("engine") or {}).get("model")
    for f in sorted(os.listdir(sl.STRATA_DIR)):
        if f.startswith("strata-") and f.endswith(".json"):
            c = sl.read_json(os.path.join(sl.STRATA_DIR, f), {}) or {}
            if c.get("model_name") == model:
                return os.path.join(sl.STRATA_DIR, f)
    return None


def set_saved(key: str, value) -> None:
    path = config_path()
    if path:                                    # our own dated copy: Strata's single .bak is overwritten each time
        os.makedirs(CONFIG_BACKUPS, exist_ok=True)
        bak = path + ".bak"
        if os.path.isfile(bak) and not state().get("bak_preserved"):    # keep the .bak that was there before
            shutil.copy2(bak, os.path.join(CONFIG_BACKUPS, os.path.basename(bak) + "-preserved-"
                                           + dt.datetime.now().strftime("%Y%m%d-%H%M%S")))
            save_state(bak_preserved=True)
        shutil.copyfile(path, os.path.join(CONFIG_BACKUPS,
                                           f"{dt.datetime.now():%Y%m%d-%H%M%S}-{os.path.basename(path)}"))
    old = get_config().get(key)
    code, body = http("POST", "/config", {"set": {key: value}})
    if code != 200:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {code}")
    save_state(pending_since=time.time())
    note("saved setting", f"{key}: {old if old is not None else 'default'} -> {value if value is not None else 'default'}"
                          f" in {body.get('file')} (used from the next full restart)", key=key, old=old, new=value)


def pending_restart() -> bool:
    since = (metrics().get("totals") or {}).get("since") or 0
    return (state().get("pending_since") or 0) > since


# ---------------------------------------------------------------- engine: unload / load
def unload() -> None:
    why = busy()
    if why:
        raise RuntimeError(why + "; try again when it is idle")
    code, body = http("POST", "/unload", {}, timeout=60)
    if code != 200:
        raise RuntimeError((body.get("error") or {}).get("message") or body.get("status") or f"HTTP {code}")
    note("engine", "engine on standby: the model is unloaded and the GPU is free; the server keeps listening")


def load() -> None:
    t0 = time.time()
    code, body = http("POST", "/load", {}, timeout=900)
    if code != 200:
        raise RuntimeError((body.get("error") or {}).get("message") or f"HTTP {code}")
    note("engine", f"engine engaged: model loaded in {time.time() - t0:.0f} s")


def restart_engine() -> None:
    unload()
    time.sleep(2)
    load()


# ---------------------------------------------------------------- the server's processes
def _processes() -> list:
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'cmd.exe','python.exe','strata.exe' } | "
          "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                         text=True, timeout=30, creationflags=NO_WINDOW).stdout
    rows = json.loads(out or "[]")
    return rows if isinstance(rows, list) else [rows]


def server_tree() -> dict:
    """The running server: its top process (the run-*.bat window, else server.py) and the .bat that started it."""
    rows = _processes()
    by_id = {r["ProcessId"]: r for r in rows}
    servers = [r for r in rows if r["Name"] == "python.exe" and "server.py" in (r.get("CommandLine") or "")
               and str(sl.STRATA_DIR).lower() in (r.get("CommandLine") or "").lower()]
    if not servers:
        return {}
    top = servers[0]
    while by_id.get(top["ParentProcessId"]) and by_id[top["ParentProcessId"]]["Name"] in ("python.exe", "cmd.exe"):
        top = by_id[top["ParentProcessId"]]
    cl = top.get("CommandLine") or ""
    hit = re.search(r'([A-Za-z]:\\[^"]+?\.bat)', cl, re.I)
    bat = hit.group(1) if hit else None
    if not bat:
        cfg = next((p.split('"')[0] for p in cl.split("--config")[1:]), "") if "--config" in cl else ""
        name = os.path.basename(cfg.strip(' "')).replace("strata-", "run-").replace(".json", ".bat")
        bat = os.path.join(sl.STRATA_DIR, name) if name else None
    return {"pid": top["ProcessId"], "name": top["Name"], "bat": bat}


def _wait_port(free: bool, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(_url("/health"), timeout=2).read()
            up = True
        except urllib.error.HTTPError:
            up = True
        except Exception:
            up = False
        if up != free:
            return True
        time.sleep(1)
    return False


def shutdown(force: bool = False) -> None:
    why = busy()
    if why and not force:
        raise RuntimeError(why + "; try again when it is idle")
    tree = server_tree()
    if not tree:
        raise RuntimeError("no Strata server process found")
    if tree.get("bat"):
        save_state(last_bat=tree["bat"])
    try:
        http("POST", "/unload", {}, timeout=60)              # let the engine give the GPU back cleanly first
    except Exception:
        pass
    subprocess.run(["taskkill", "/PID", str(tree["pid"]), "/T", "/F"], capture_output=True, timeout=30,
                   creationflags=NO_WINDOW)
    _wait_port(free=True, secs=30)
    note("engine", f"Strata shut down (process tree {tree['pid']}, {tree['name']})")


def panel_bat(bat: str) -> str:
    """A copy of the user's run-*.bat without "--open", so a start from the panel doesn't open the browser (the
    panel must not take the user out of it). The user's own .bat is left as it is."""
    with open(bat, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    out = [re.sub(r'\s+"?--open"?(?=\s|$)', "", ln) if "server.py" in ln else ln for ln in lines]
    path = os.path.join(sl.LOG_DIR, "panel-start-" + os.path.basename(bat))
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out) + "\n")
    return path


def launch(bat: str) -> None:
    """Run a .bat in its own minimized console window. `start` takes the first quoted argument as the window's
    title, so the title is quoted and so is the path."""
    subprocess.Popen(f'cmd /c start "Strata" /min "{bat}"', cwd=sl.STRATA_DIR, creationflags=NO_WINDOW)


def start_server() -> None:
    bat = state().get("last_bat") or (server_tree() or {}).get("bat") or os.path.join(sl.STRATA_DIR, "run-iq3_xxs.bat")
    if not os.path.isfile(bat):
        raise RuntimeError(f"{bat} not found")
    launch(panel_bat(bat))
    t0 = time.time()
    if not _wait_port(free=False, secs=120):
        raise RuntimeError(f"started {os.path.basename(bat)}, but the server did not answer within 2 min")
    for _ in range(600):                                 # then wait for the model (the server answers before READY)
        st = (metrics().get("live") or {}).get("state")
        if st in ("idle", "generating", "reading"):
            break
        time.sleep(1)
    note("engine", f"Strata started from {os.path.basename(bat)} in {time.time() - t0:.0f} s")


def full_restart() -> None:
    shutdown()
    time.sleep(3)
    start_server()


# ---------------------------------------------------------------- speed trials
_SHORT = ("You are being timed. Write a plain, factual description of how a warp core works in a starship, "
          "covering the matter and antimatter injectors, the dilithium chamber, the warp coils and the coolant. "
          "Keep going until you are stopped. ")
_FILLER = ("Engineering log, maintenance cycle {n}: the plasma conduits on deck {d} were inspected, the coolant "
           "pressure read {p} kilopascals, and the containment field held steady at {f} percent. ")


def _prefill_prompt(tokens: int) -> str:
    # about 40 tokens per sentence; varied numbers so nothing repeats exactly
    parts = [_FILLER.format(n=i, d=i % 42 + 1, p=300 + (i * 37) % 200, f=90 + (i * 7) % 10) for i in range(tokens // 40)]
    return "".join(parts) + "\nIn one sentence, what was inspected?"


def _one_trial(kind: str) -> dict:
    nonce = f"[trial {time.time():.3f}] "             # makes the prompt new each time: no cached prefix to reuse
    if kind == "short":
        prompt, max_tokens = nonce + _SHORT, 256
    else:
        prompt, max_tokens = nonce + _prefill_prompt(8000), 32
    m0 = metrics()
    model = (m0.get("engine") or {}).get("model") or "strata"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": "none", "stream": True}
    req = urllib.request.Request(_url("/v1/chat/completions"), data=json.dumps(body).encode(), method="POST",
                                 headers=_headers())
    t0 = time.time()
    first = None
    chunks = 0
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                d = json.loads(line[5:])
            except ValueError:
                continue
            delta = ((d.get("choices") or [{}])[0].get("delta") or {})
            if delta.get("content") or delta.get("reasoning_content"):
                chunks += 1
                if first is None:
                    first = time.time()
    t1 = time.time()
    # Strata's own numbers for the request (it shows in /metrics within a second or two)
    rq = None
    for _ in range(10):
        time.sleep(0.5)
        with sl.lock:
            reqs = (sl.state.get("metrics") or {}).get("requests") or []
        cand = [x for x in reqs if (x.get("time") or 0) >= t0 - 1]
        if cand:
            rq = max(cand, key=lambda x: x.get("time") or 0)
            break
    hw = (metrics().get("hardware") or {})
    out = {"kind": kind, "model": model, "ttft_s": round(first - t0, 2) if first else None,
           "wall_s": round(t1 - t0, 2)}
    if rq:
        pr = rq.get("prompt_read") or rq.get("prompt_tokens") or 0
        out.update(prompt_tokens=rq.get("prompt_tokens"), prompt_read=pr, output_tokens=rq.get("output_tokens"),
                   prefill_tok_s=round(pr / (rq["prompt_ms"] / 1000), 1) if rq.get("prompt_ms") else None,
                   decode_tok_s=rq.get("decode_tok_s"), hit_rate=rq.get("hit_rate"), pcie_share=rq.get("pcie_share"),
                   drafts=f"{rq.get('drafts_accepted', 0)}/{rq.get('drafts_offered', 0)}")
    out.update(gpu_temp=hw.get("gpu_temp"), gpu_power=hw.get("gpu_power"))
    return out


def speed_trial(kind: str) -> dict:
    """kind: short | prefill | full. Saved to logs\\speed-trials.jsonl; the result is returned."""
    why = busy()
    if why:
        raise RuntimeError(why + "; speed trials run only when Strata is idle")
    runs = []
    plan = {"short": ["short"], "prefill": ["prefill"], "full": ["short", "prefill"] * 3}[kind]
    for i, k in enumerate(plan):
        action["note"] = f"{k.upper()} RUN {i + 1} OF {len(plan)}"
        runs.append(_one_trial(k))

    def med(k, key):
        v = sorted(x[key] for x in runs if x["kind"] == k and x.get(key) is not None)
        return v[len(v) // 2] if v else None
    eng = metrics().get("engine") or {}
    rec = {"ts": time.time(), "trial": kind, "model": eng.get("model"), "engine": eng.get("version"),
           "decode_tok_s": med("short", "decode_tok_s"), "ttft_s": med("short", "ttft_s"),
           "prefill_tok_s": med("prefill", "prefill_tok_s"), "hit_rate": med("short", "hit_rate"),
           "pcie_share": med("short", "pcie_share"), "gpu_temp": max((r.get("gpu_temp") or 0) for r in runs),
           "live_tuning": get_shared(), "args": {k: eng.get(k) for k in ("spec", "mtp_max", "pcie_frac",
                                                                          "expert_slots", "kv", "context")},
           "runs": runs}
    with open(TRIALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    bits = []
    if rec["decode_tok_s"]:
        bits.append(f"{rec['decode_tok_s']:.1f} tok/s writing")
    if rec["prefill_tok_s"]:
        bits.append(f"{rec['prefill_tok_s']:.0f} tok/s prefill")
    if rec["ttft_s"] is not None:
        bits.append(f"first token {rec['ttft_s']:.1f} s")
    note("speed trial", f"{kind} trial: " + ", ".join(bits))
    return rec


def trials(limit: int = 200) -> list:
    try:
        with open(TRIALS_LOG, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:]


def tuning_history(limit: int = 50) -> list:
    try:
        with open(TUNING_LOG, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:]
