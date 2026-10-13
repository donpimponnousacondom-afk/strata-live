"""Reports for the captain: what Strata did over a period, written quietly to logs\\reports\\ as HTML (with charts
drawn into the file, no web server) and Markdown. Nothing opens by itself."""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import statistics
import time

import strata_control as ctl
import strata_live as sl

REPORT_DIR = os.path.join(sl.LOG_DIR, "reports")
KINDS = ("coding turn", "summary", "failed summary", "full re-read")
COLORS = {"coding turn": "#2f9e6e", "summary": "#e07b4f", "failed summary": "#d63a2f", "full re-read": "#d9a52b"}


def period(name: str, since_start: float | None = None) -> tuple:
    now = time.time()
    if name == "hour":
        return now - 3600, now, "Last hour"
    if name == "shift":
        return now - 8 * 3600, now, "This shift (8 hours)"
    if name == "today":
        return dt.datetime.combine(dt.date.today(), dt.time()).timestamp(), now, "Today"
    return since_start or now - 86400, now, "Since Strata started"


def file_events(t0: float, t1: float) -> list:
    out = []
    try:
        with open(os.path.join(sl.LOG_DIR, "events.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if t0 <= (e.get("ts") or 0) <= t1:
                    out.append(e)
    except OSError:
        pass
    return out


def build(an, name: str) -> dict:
    with an.lock:
        snap = dict(an.snap)
        reqs = list(an.reqs)
        comps = list(an.compactions)
    tot = snap.get("totals") or {}
    t0, t1, label = period(name, tot.get("since"))
    rs = [r for r in reqs if t0 <= (r.get("time") or 0) <= t1]
    busy = dict.fromkeys(KINDS, 0.0)
    for r in rs:
        busy[an.kind(r)] += r.get("duration_s") or 0
    total = sum(busy.values())
    tok = [r["decode_tok_s"] for r in rs if r.get("decode_tok_s") and (r.get("output_tokens") or 0) >= 20]
    pre = [r["prompt_read"] / (r["prompt_ms"] / 1000) for r in rs if r.get("prompt_ms") and
           (r.get("prompt_read") or 0) >= 200]
    prompt = sum(r.get("prompt_tokens") or 0 for r in rs)
    reused = sum(r.get("reused") or 0 for r in rs)
    evs = file_events(t0, t1)
    warn = {}
    for e in evs:
        if e.get("level") in ("warn", "error") and e.get("source") != "control":
            warn[e["kind"]] = warn.get(e["kind"], 0) + 1
    cs = [c for c in comps if t0 <= c["ts"] <= t1]
    trials = [t for t in ctl.trials(500) if t0 <= t["ts"] <= t1]
    tuning = [t for t in ctl.tuning_history(500) if t0 <= t["ts"] <= t1]
    hw = snap.get("hardware") or {}
    hours = {}
    for r in rs:
        h = dt.datetime.fromtimestamp(r["time"]).strftime("%H:00")
        hours.setdefault(h, dict.fromkeys(KINDS, 0.0))
        hours[h][an.kind(r)] += (r.get("duration_s") or 0) / 60
    share = (total - busy["coding turn"]) / total if total else 0
    errors = sum(1 for e in evs if e.get("level") == "error")
    verdict = ("Strata was offline at the time of the report." if not snap.get("up") else
               f"{len(rs)} requests, {total / 60:.0f} minutes busy, {share:.0%} of it on compaction. "
               + ("No errors." if not errors else f"{errors} error(s) — see the warnings."))
    return {
        "name": name, "label": label, "t0": t0, "t1": t1, "made": time.time(),
        "model": (snap.get("engine") or {}).get("model"), "engine": (snap.get("engine") or {}).get("version"),
        "verdict": verdict, "requests": len(rs), "first_req": rs[0]["time"] if rs else None, "busy_min": total / 60, "busy": busy, "share": share,
        "tok_median": statistics.median(tok) if tok else None, "pre_median": statistics.median(pre) if pre else None,
        "tok_series": [(r["time"], r["decode_tok_s"]) for r in rs if r.get("decode_tok_s") and
                       (r.get("output_tokens") or 0) >= 20],
        "prompt_tokens": prompt, "reuse": reused / prompt if prompt else None,
        "written": sum(r.get("output_tokens") or 0 for r in rs),
        "cut": sum(1 for r in rs if r.get("finish") == "length"),
        "compactions": [{"ts": c["ts"], "before": c.get("before"), "words": c["words"],
                         "by": "extension" if c["by_extension"] else "pi default"} for c in cs],
        "warnings": warn, "errors": errors, "trials": trials, "tuning": tuning, "hours": hours,
        "now_hw": {"gpu_temp": hw.get("gpu_temp"), "vram_free_mib": ((hw.get("gpu_mem_total") or 0) -
                                                                      (hw.get("gpu_mem_used") or 0)) / 1048576
                   if hw.get("gpu_mem_total") else None, "ram_gb": (hw.get("ram_used") or 0) / 1e9},
        "checks": snap.get("checks") or [],
    }


def _svg_line(points, w=720, h=180, color="#2f6fb0", label=""):
    if len(points) < 2:
        return "<p class='muted'>Not enough requests to chart.</p>"
    t0, t1 = points[0][0], points[-1][0]
    top = max(v for _, v in points) * 1.15
    xy = " ".join(f"{40 + (w - 50) * (t - t0) / max(1, t1 - t0):.1f},{h - 20 - (h - 40) * v / top:.1f}"
                  for t, v in points)
    ticks = "".join(f"<text x='34' y='{h - 20 - (h - 40) * k / 4 + 4:.0f}' text-anchor='end'>{top * k / 4:.0f}</text>"
                    f"<line x1='40' x2='{w - 10}' y1='{h - 20 - (h - 40) * k / 4:.0f}' "
                    f"y2='{h - 20 - (h - 40) * k / 4:.0f}' class='grid'/>" for k in range(5))
    lab = (f"<text x='40' y='{h - 4}'>{dt.datetime.fromtimestamp(t0):%H:%M}</text>"
           f"<text x='{w - 10}' y='{h - 4}' text-anchor='end'>{dt.datetime.fromtimestamp(t1):%H:%M}</text>")
    return (f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(label)}'>{ticks}{lab}"
            f"<polyline points='{xy}' fill='none' stroke='{color}' stroke-width='2'/></svg>")


def _svg_hours(hours, w=720, h=180):
    if not hours:
        return "<p class='muted'>No requests in this period.</p>"
    keys = sorted(hours)
    top = max(sum(v.values()) for v in hours.values()) * 1.15 or 1
    bw = (w - 50) / len(keys)
    out = []
    for i, k in enumerate(keys):
        y = h - 20
        for kind in KINDS:
            v = hours[k][kind]
            hh = (h - 40) * v / top
            if hh > 0.5:
                out.append(f"<rect x='{40 + i * bw + 2:.1f}' y='{y - hh:.1f}' width='{bw - 4:.1f}' height='{hh:.1f}' "
                           f"fill='{COLORS[kind]}'><title>{k} {kind}: {v:.1f} min</title></rect>")
                y -= hh + 1
        out.append(f"<text x='{40 + i * bw + bw / 2:.1f}' y='{h - 4}' text-anchor='middle'>{k[:2]}</text>")
    ticks = "".join(f"<text x='34' y='{h - 20 - (h - 40) * j / 4 + 4:.0f}' text-anchor='end'>{top * j / 4:.0f}</text>"
                    for j in range(5))
    return f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='Busy minutes per hour'>{ticks}{''.join(out)}</svg>"


def write(rep: dict) -> tuple:
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = dt.datetime.fromtimestamp(rep["made"]).strftime("%Y%m%d-%H%M%S")
    base = os.path.join(REPORT_DIR, f"report-{stamp}-{rep['name']}")
    f = lambda t: dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    rows = lambda pairs: "".join(f"<tr><td>{html.escape(str(a))}</td><td>{html.escape(str(b))}</td></tr>"
                                 for a, b in pairs)
    facts = [
        ("Period", f"{f(rep['t0'])} to {f(rep['t1'])}" + (f" (request history from {f(rep['first_req'])})"
                                                          if rep.get("first_req") and rep["first_req"] - rep["t0"] > 900
                                                          else "")),
        ("Model", f"{rep['model']} · engine {rep['engine']}"),
        ("Requests", f"{rep['requests']:,} · busy {rep['busy_min']:.0f} min"),
        ("Writing speed (median)", f"{rep['tok_median']:.1f} tok/s" if rep["tok_median"] else "—"),
        ("Prefill speed (median)", f"{rep['pre_median']:,.0f} tok/s" if rep["pre_median"] else "—"),
        ("Prompt tokens", f"{rep['prompt_tokens']:,}" + (f" · {rep['reuse']:.1%} from the cache"
                                                          if rep["reuse"] is not None else "")),
        ("Tokens written", f"{rep['written']:,} · {rep['cut']} replies cut at their limit"),
        ("Time on compaction", f"{rep['share']:.0%}"),
        ("GPU now", f"{rep['now_hw']['gpu_temp']} °C · {rep['now_hw']['vram_free_mib'] or 0:,.0f} MiB VRAM free"),
    ]
    split = "".join(f"<span style='flex:{max(rep['busy'][k], 0.001):.1f};background:{COLORS[k]}' "
                    f"title='{k}: {rep['busy'][k] / 60:.1f} min'></span>" for k in KINDS)
    legend = "".join(f"<li><i style='background:{COLORS[k]}'></i>{k}: {rep['busy'][k] / 60:.1f} min</li>"
                     for k in KINDS)
    comp = "".join(f"<tr><td>{f(c['ts'])}</td><td>{(c['before'] or 0):,}</td><td>{c['words']:,}</td>"
                   f"<td>{c['by']}</td></tr>" for c in rep["compactions"]) or \
        "<tr><td colspan='4' class='muted'>None</td></tr>"
    warn = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in
                   sorted(rep["warnings"].items(), key=lambda kv: -kv[1])) or \
        "<tr><td colspan='2' class='muted'>None</td></tr>"
    trials = "".join(f"<tr><td>{f(t['ts'])}</td><td>{t['trial']}</td><td>{t.get('decode_tok_s') or '—'}</td>"
                     f"<td>{t.get('prefill_tok_s') or '—'}</td></tr>" for t in rep["trials"]) or \
        "<tr><td colspan='4' class='muted'>None</td></tr>"
    tuning = "".join(f"<tr><td>{f(t['ts'])}</td><td>{html.escape(t['text'])}</td></tr>" for t in rep["tuning"]) or \
        "<tr><td colspan='2' class='muted'>None</td></tr>"
    checks = "".join(f"<li class='{lv}'>{html.escape(t)}</li>" for lv, t in rep["checks"])
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strata report · {html.escape(rep['label'])}</title><style>
:root{{--bg:#f6f5f2;--panel:#fff;--ink:#1f2328;--muted:#646b73;--line:#dfe1e4;--accent:#c46a00}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111316;--panel:#1a1d21;--ink:#e6e8eb;--muted:#9aa1a9;--line:#2c3036;--accent:#ff9900}}}}
body{{background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"Segoe UI",sans-serif;margin:0}}
main{{max-width:820px;margin:0 auto;padding:32px 16px 56px}} h1{{margin:0 0 4px}} h2{{margin:28px 0 10px;font-size:1.15rem}}
.muted{{color:var(--muted)}} .verdict{{border-left:4px solid var(--accent);padding:8px 14px;background:var(--panel)}}
table{{width:100%;border-collapse:collapse;background:var(--panel);font-variant-numeric:tabular-nums}}
td,th{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}} td:first-child{{color:var(--muted)}}
.bar{{display:flex;height:22px;gap:2px;border-radius:5px;overflow:hidden}} .bar span{{display:block}}
ul.legend{{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:6px 18px}} ul.legend i{{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px}}
svg{{width:100%;height:auto;background:var(--panel);border:1px solid var(--line);border-radius:6px}} svg text{{font-size:11px;fill:var(--muted)}} svg .grid{{stroke:var(--line)}}
li.warn{{color:#b07800}} li.bad{{color:#c0392b}}</style></head><body><main>
<p class="muted">Strata Engineering · report made {f(rep['made'])}</p>
<h1>{html.escape(rep['label'])}</h1><p class="verdict">{html.escape(rep['verdict'])}</p>
<h2>At a glance</h2><table>{rows(facts)}</table>
<h2>Where the busy time went</h2><div class="bar">{split}</div><ul class="legend">{legend}</ul>
<h2>Busy minutes per hour</h2>{_svg_hours(rep['hours'])}
<h2>Writing speed per request (tok/s)</h2>{_svg_line(rep['tok_series'], label='Writing speed per request')}
<h2>Compactions</h2><table><tr><th>When</th><th>At tokens</th><th>Summary words</th><th>Written by</th></tr>{comp}</table>
<h2>Warnings and errors</h2><table><tr><th>Kind</th><th>Count</th></tr>{warn}</table>
<h2>Speed trials</h2><table><tr><th>When</th><th>Trial</th><th>Writing tok/s</th><th>Prefill tok/s</th></tr>{trials}</table>
<h2>Changes made from the panel</h2><table><tr><th>When</th><th>Change</th></tr>{tuning}</table>
<h2>Settings check at the time of the report</h2><ul>{checks}</ul>
</main></body></html>"""
    with open(base + ".html", "w", encoding="utf-8") as fh:
        fh.write(doc)
    md = [f"# Strata report: {rep['label']}", "", f"Made {f(rep['made'])}.", "", f"> {rep['verdict']}", ""]
    md += ["| | |", "|---|---|"] + [f"| {a} | {b} |" for a, b in facts] + [""]
    md += ["## Busy time", ""] + [f"- {k}: {rep['busy'][k] / 60:.1f} min" for k in KINDS] + [""]
    md += ["## Compactions", ""] + ([f"- {f(c['ts'])}: at {(c['before'] or 0):,} tokens, {c['words']} words, "
                                     f"{c['by']}" for c in rep["compactions"]] or ["- none"]) + [""]
    md += ["## Warnings", ""] + ([f"- {k}: {v}" for k, v in rep["warnings"].items()] or ["- none"]) + [""]
    md += ["## Speed trials", ""] + ([f"- {f(t['ts'])} {t['trial']}: {t.get('decode_tok_s')} tok/s writing, "
                                      f"{t.get('prefill_tok_s')} prefill" for t in rep["trials"]] or ["- none"]) + [""]
    md += ["## Changes from the panel", ""] + ([f"- {f(t['ts'])}: {t['text']}" for t in rep["tuning"]] or ["- none"])
    with open(base + ".md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(rep, fh)
    return base + ".html", base + ".md"


def listing(limit: int = 20) -> list:
    try:
        names = sorted((n for n in os.listdir(REPORT_DIR) if n.endswith(".json")), reverse=True)[:limit]
    except OSError:
        return []
    return [os.path.join(REPORT_DIR, n) for n in names]
