# Strata Engineering console: UI handoff

The running notes for the console's UI only. We add to them as we go; the newest notes are at the bottom. The overall
notes on the Strata install are in `D:\strata\Handoff.md`.

**Now:** revision **0.4.0** (`version.py`), 2026-10-13 ~18:00. It's built and running. The user likes what they've
seen so far and will test the live controls later.

---

## 1. What it is

- The desktop console for watching **and running** Strata. The user is the chief engineer, Strata is the warp core,
  and the sidebar buttons are the engineering departments.
- **Start:** double-click `start-strata-monitor.bat` (pythonw, no console window).
- **Options:**
  - `--page=N` opens on department N
  - `--classic` gives the plain window
  - `--selftest` builds the window, refreshes it, prints the readouts and closes
- tkinter only: nothing to install and no web server.
- The plan it was built from: `UI-PLAN.md` (version 2, approved).

## 2. Rules (from the user)

- **No upload.** No GitHub repo and no push until the user says so. The git repository here is local only (section 9).
- **Never leave the panel.** Nothing opens on its own: no browser, no Explorer, no pop-ups. Files are saved quietly
  with a one-line status. Only "open the log folder" and "open this report in the browser" leave the panel, and both
  say so.
- **The panel may change Strata only when clicked.** Anything that changes Strata takes two clicks. Engine controls
  are refused while a request runs. Every change is logged to `logs\tuning.jsonl` and the ship's log. The panel never
  edits pi.
- **Back up before editing any file here** (`backups\<time>\`). Before every saved setting change, the panel itself
  copies the Strata config to `backups\config\`.
- **Keep it fitting 1280×780.** That is the size the user runs it at. Check it at 1560×900 as well.

## 3. Files

| File | What it does |
|---|---|
| `strata_monitor.pyw` | the entry point: the `Analyzer` (requests, kinds, compactions, pi session, checks, alarms), the snapshot the window draws, the options |
| `lcars_window.py` | the frame: header (condition, last control result), the department sidebar, the warp core and its animation (20/s), the widgets (button with two-click confirm, slider, chart, gauge, tiles, table), input, red alert |
| `lcars_theme.py` | the colours, `mix()`, `coil_mix()` (cache / RAM / PCIe fractions from hit_rate and pcie_share) |
| `pages\*.py` | one file per department; each has `build(area)` (static) and `draw(snapshot)` (once a second) |
| `strata_control.py` | everything that acts on Strata: shared settings, `/config`, unload/load, process tree, start (`panel-start-*.bat` without `--open`), speed trials, `tuning.jsonl` |
| `report_maker.py` | the reports: HTML (inline SVG charts) + Markdown + JSON in `logs\reports\` |
| `version.py` | the revision |
| `tools\grab_window.py` | a screenshot of the console window (PrintWindow, so it works while the window is covered) |
| `tools\screenshot_pages.py` | selftests each page and screenshots it; reports tracebacks |
| `strata_live.py` | the watchers (shared with the old web page): `/metrics` poll, logs, configs, Windows events, events, incidents, bundle |

## 4. The departments (F1–F9)

| # | Page | Main graphic | Controls |
|---|---|---|---|
| 01 | Main engineering | readouts, bars, 2-min traces, conversation gauge, duty ring, settings check, latest log | log scroll and click |
| 02 | Warp core | coil map of the last 40 requests (cache / RAM / PCIe), tok/s, prefill and hit-rate charts, last 12 requests, engine config, speed by day | — |
| 03 | Core control | engine buttons | STANDBY, ENGAGE, RESTART ENGINE, FULL RESTART, SHUT DOWN / START; live sliders; saved-settings sliders; RESTORE DEFAULTS |
| 04 | Power & coolant | SSD ▸ RAM ▸ PCIe ▸ GPU flow, 4 gauges, last-minute graphs, hardware events | — |
| 05 | Comms · pi link | conversation-size sawtooth with compactions marked, compaction table, link tiles | — |
| 06 | Diagnostics | the scan bar over the core, ✓ ! ✕ marks on the parts | levels 5–1; SHORT, PREFILL and FULL speed trials |
| 07 | Ship's log | every event, warnings per hour | source filters, warnings only, requests hidden/shown, incidents |
| 08 | Reports | the report drawn on the page | last hour, shift, today, since start; open in browser |
| 09 | Maintenance | counters (moved from the sidebar), log folder, monitor health | bundle, always on top, open the log folder |

**Core colours**

- Coils, from the last request's experts:
  - blue-white: from the card's cache
  - orange: loaded from RAM
  - violet: over PCIe
- The whole core, by state:

  | State | Colour |
  |---|---|
  | reading | amber |
  | full re-read | gold |
  | speed trial or diagnostic scan | green |
  | powering up or down | rings in turn |
  | standby | dark, STANDBY plate |
  | VRAM < 400 MiB or GPU ≥ 85 °C | orange tint, flickering field |
  | red alert | red |
  | offline | dark red |

- Strata reports the expert mix only per finished request, and only as totals. Lighting coils per expert would need a
  change in Strata.

## 5. How the controls reach Strata (checked in `serve/server.py`)

- **Live tuning:** `POST /settings` `{"defaults": {...}}`. It replaces the whole set, so the panel merges first.
  - Sampling is `{config sampling, then shared, then the request's own}` (line ~3676). The thinking budget is read
    after the merge. pi sends no temperature, top_p or top_k, so the sliders reach pi.
  - pi always sends `max_tokens`, so the reply-limit slider only affects other apps.
  - A change to `reasoning_effort` costs pi one full re-read (it goes at the start of the prompt).
  - Saved by Strata to `strata-iq3_xxs.shared-settings.json`, and kept across restarts.
- **Saved settings:** `POST /config` `{"set": {...}}`. Strata checks each value and uses it from the next start. It
  writes `<config>.bak`, overwriting it each time. The user's earlier `.bak` was copied to `backups\config\` first.
- **Standby / engage:** `/unload`, `/load` (409 while busy).
- **Shut down:** unload, then `taskkill /T /F` on the top of the tree (the `cmd.exe` running `run-iq3_xxs.bat`).
- **Start:** `cmd /c start "Strata" /min "<logs\panel-start-run-iq3_xxs.bat>"`, a copy of the user's `.bat` without
  `--open`. The user's own `.bat` is unchanged.
- **Speed trials:** a streamed `/v1/chat/completions` with temperature 0, `reasoning_effort` none, and a fresh prompt
  (a timestamp first) so nothing is reused. The numbers come from Strata's `/metrics` record of the request.
  - **Cost:** the conversation cache is off, so pi's next turn re-reads its whole conversation (about 1–2 min).

## 6. Testing

- **Each page:** `python tools\screenshot_pages.py [out_dir] [1,3,6] [1280x780]`. This runs selftests and reports
  tracebacks.
- **The running console:** `python tools\grab_window.py out.png 1280 780` (this also resizes the window).
- **After any change:**
  - `py_compile` all files
  - restart through the `.bat`
  - look for "Traceback" in `logs\monitor.log` (`refresh` and `animate` catch and print, so a broken page shows only
    there and as "THIS PAGE HIT AN ERROR")

## 7. Status

**Done and checked:**
- all 9 pages at 1280×780 (no tracebacks, layout fixes in)
- the live console
- reports (HTML checked in a browser)
- a live tuning round trip (temperature 0.85 and cleared)
- finding Strata's process and `.bat`
- the launcher's quoting (scratch `.bat`)
- the `--open` strip

**Not yet run for real.** The user will test these, while pi is idle:

| Test | Expect |
|---|---|
| SHORT speed trial | a green core, "TRIAL SAVED" in the header, a row in `speed-trials.jsonl`, the chart on page 06 |
| PREFILL and FULL trials | FULL takes several minutes (6 runs) |
| STANDBY then ENGAGE | dark core and STANDBY plate, then gold rings powering up; note the load time |
| A saved setting (e.g. engine silence 300) | PENDING light; a copy in `backups\config\` |
| FULL RESTART | Strata down for the load time, back in a minimized window, no browser; PENDING clears |
| SHUT DOWN, then START STRATA | the same, in two steps |
| Diagnostic levels 5, 4, 3 | read-only, quick |
| Diagnostic levels 2 and 1 | include trials; level 1 also saves a bundle |

## 8. Known issues and ideas

- **"Today" in reports** covers requests only from 13:57 on 2026-10-13, when the monitor started saving them. The
  report says so.
- **Ship's log** shows the events in memory (the last 600). Reports read the whole of `events.jsonl`.
- **Edit misses** are counted from pi's tool-result text ("could not find", "not found", "no match"); that's a
  heuristic.
- **Not done yet:**
  - editing the engine's arguments (read-only by decision; it would need a backup and a trial before and after)
  - Strata's opt-in conversation cache, so a trial doesn't cost pi a re-read (needs its own VRAM test)
  - a red-alert run with the new shapes, seen live
- **Hardware seen while building it:** free VRAM fell to 239 MiB and the GPU hit 85 °C six times in an hour under
  pi's load. Both are shown on the panel; nothing was changed.

## 9. Revisions

Local git repository in `D:\strata-live`, with no remote. `.gitignore` keeps out `logs/`, `backups/`, `out/`,
`CHANGES-*.md` and `.claude/`. Bump `version.py`, add a row here, and commit.

| Rev | When | What |
|---|---|---|
| 0.1.0 | 2026-10-13 14:15 | desktop window in place of the web page (tkinter), the new checks (CHANGES §1, §2) |
| 0.2.0 | 14:45 | the engineering-console look: warp core, bars, traces, ship's log (§1b) |
| 0.3.0 | 16:40 | second pass from the user's reference images: leader lines, orbit, readouts, duty ring (§1c) |
| 0.4.0 | 17:45 | the console: 9 departments, engine control, live and saved tuning, speed trials, diagnostics levels, reports, maintenance; start and `--open` fixes (§1d) |

Revisions 0.1–0.3 were before git. Their files are in `backups\20261013-141320\`, `backups\20261013-163617\` and
`backups\20261013-172621-before-console\`. The first commit is 0.4.0.

## 10. Notes log (newest at the bottom)

### 2026-10-13 ~18:00: handoff started
- The user likes what they've seen and will test the live controls later.
- They asked for this file and for revision control. Local git was set up at 0.4.0.
