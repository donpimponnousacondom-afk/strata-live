# Strata Monitor: engineering console redo, the plan

2026-10-13 · version 2, with your additions · for review · no code changed yet

**The idea:** you're the chief engineer, Strata is the engine, and this is your panel. From it you watch the engine,
diagnose it, test it, tune it, stop and start it, and pull a report when the captain asks.

You never have to leave the panel. Everything opens inside it: no browser, no Explorer, no pop-up boxes. Files (test
results, reports, bundles) are saved quietly, and a single status line on the screen says so.

The main board stays as it is: warp core, systems, operations. The left-side buttons become departments. Clicking one
keeps the warp core on the left and swaps the right side for that department's screen.

---

## 1. What Strata already lets us do (checked in its code)

| You want | How Strata allows it | When it takes effect |
|---|---|---|
| Change temperature and other sampling settings | `POST /settings`, Strata's shared settings. Keys: temperature, top_p, top_k, reasoning_effort, reasoning_budget_tokens, seed, max_tokens, speed projection. They fill in whatever a request leaves out. pi sends none of them, so they apply to pi. | **at once**, from the next request |
| Change saved settings | `POST /config`, the keys the Settings page lists: sampling defaults, vram_reserve_mib, idle_unload_s, engine_silence_s, reasoning_budget_tokens and others. Strata checks every value and keeps a copy of the old file itself. | at the **next full restart** |
| Stop the engine, keep the server up | `POST /unload`: frees the GPU, and the next request or `/load` brings it back. Refused while a request is running. | at once |
| Start the engine | `POST /load` | at once (the model takes a while to load) |
| Restart or shut down everything | Strata has no endpoint for this. The panel would stop the server's processes (`run-iq3_xxs.bat` → server.py → strata.exe), unloading first, and start `run-iq3_xxs.bat` again in a minimized window. | |
| Engine arguments (`--spec`, `--pcie-frac`, `--expert-cache`, `--max-context`, `--kv`) | Strata's settings API can't change these; they are in the config's `args` list. | shown **read-only** (see decisions) |
| Measure speed | a normal chat request with a fixed prompt, timed by the panel, plus Strata's own per-request numbers (prefill time, decode tok/s, hit rate, PCIe share) | |

All of these go to 127.0.0.1 only, as now.

## 2. Everything we log or see, and its home

| Source | Home |
|---|---|
| `/metrics` engine settings, live state, last 12 requests, `requests-<date>.jsonl`, conversation cache, `speed-history.json`, engine warnings (runaway reply, reply cut, near context limit, long request, slow tool call, server) | **Warp core** |
| shared settings, `/config` keys, engine arguments, load/unload | **Core control** |
| `/metrics` hardware, 60-sample history, hardware name plate; events gpu hot, vram low, ram full; Windows unexpected shutdowns | **Power & coolant** |
| pi sessions: conversation size, compactions, thinking leaks, timeouts, aborts, replies cut, bash errors, edit misses (new count); events pi compacted, conversation shortened, timeout, app hung up | **Comms · pi link** |
| settings checks, config changes, `/status` `/slots` `/settings` `/config` read back, speed trials (new: `logs\speed-trials.jsonl`), diagnostic results (new: `logs\diagnostics.jsonl`) | **Diagnostics** |
| every event; the 73 incident snapshots | **Ship's log** |
| reports (new: `logs\reports\`) | **Reports** |
| totals and uptime, log folder, bundles, `monitor.log`, monitor events (started, two monitors, bundle made, incident saved), always on top | **Maintenance** |

## 3. The sidebar: 9 departments

The STRATA and PI lights become a **ship's condition** in the header: GREEN; YELLOW, a warning in the last 10 min;
RED, Strata down or an error. The counters move to Maintenance.

| # | Button (and its plain name) | Key |
|---|---|---|
| 01 | MAIN ENGINEERING · overview | F1 |
| 02 | WARP CORE · the engine in depth | F2 |
| 03 | CORE CONTROL · start, stop, tune | F3 |
| 04 | POWER & COOLANT · hardware | F4 |
| 05 | COMMS · PI LINK · pi and the conversation | F5 |
| 06 | DIAGNOSTICS · checks and speed trials | F6 |
| 07 | SHIP'S LOG · every event | F7 |
| 08 | REPORTS · for the captain | F8 |
| 09 | MAINTENANCE · upkeep | F9 |

A button lights up while its screen is open, and blinks if its department has an unread warning. Nine buttons fit at
1280×780.

## 4. The screens

The warp core is on the left of every screen, and the screen fills the right.

**01 Main engineering.** Today's board, tidied.

**02 Warp core**
- A large **coil map**, coloured by where the last request's experts came from (section 5).
- Graphs per request: tok/s, prefill speed, hit rate, PCIe share, drafts accepted. Speed per day from `speed-history`.
- The last 12 requests in a table, and the engine's settings.

**03 Core control** (new: your idea)
- **Engine buttons** along the top: STANDBY (unload) · ENGAGE (load) · RESTART ENGINE (unload and load) · FULL
  RESTART (server too) · SHUT DOWN.
  - Each one takes **two clicks**: the first turns the button red and shows "CONFIRM ▸ pi's turn will wait about
    N s". There is no pop-up.
  - Each is refused, with a reason, while a request is running. The core shows each one: the rings power down to dark
    and power back up in sequence.
- **Live tuning**, with **sliders**, taking effect from the next request:
  - temperature 0–2
  - top_p 0.05–1
  - top_k 1–64
  - thinking: none / low / medium / high
  - thinking budget 0–32K tokens
  - default reply limit
  - Each slider shows the value Strata is using now, and the default.
- **Saved settings** (sliders and switches), used from the next full restart:
  - VRAM reserve
  - idle unload
  - engine silence limit
  - the sampling defaults in the config
  - A "PENDING RESTART" light shows when these differ from what's running.
- **Engine arguments**, read-only: spec, PCIe share, expert cache, context, KV.
- Every change goes to the ship's log and to `logs\tuning.jsonl`, with the old and new value. One button, **RESTORE
  DEFAULTS**, clears the live tuning.

**04 Power & coolant**
- A flow diagram: SSD → RAM → PCIe → GPU, with live MB/s.
- Gauges for temperature (85 °C), power (against the limit), free VRAM (250 and 400 MiB) and the PCIe link.
- 60-sample graphs, the name plate, and the hardware events.

**05 Comms · pi link**
- A live **sawtooth chart** of the conversation size, with each compaction marked.
- The compaction table.
- Counters: thinking leaks, timeouts, replies cut, bash errors, edit misses.
- pi's own settings.

**06 Diagnostics**
- The core with a **scan sweep**: a bar moves down the core and marks each part ✓ ! or ✕ as its check runs.
- **Levels**, where 1 is the most thorough:

| Level | Does | Sends to Strata? |
|---|---|---|
| 5 | settings checks | no |
| 4 | + today's logs scanned, config files checked against the last backup | no |
| 3 | + Strata's `/status`, `/slots`, `/settings`, `/config` read back and compared | no |
| 2 | + a **short speed trial** | yes |
| 1 | + the **full speed trial**, and a diagnostics bundle | yes |

- **Speed trials (dyno runs)**: your test button.
  - SHORT: a fixed 300-token prompt, 256 tokens written. It measures time to first token and writing tok/s.
  - PREFILL: a fixed 8,000-token prompt, 32 written. It measures prefill tok/s.
  - FULL: both, three times each, and gives the median.
  - Every trial uses the same prompt, temperature 0 and thinking off, so runs can be compared.
- Each trial is **saved quietly** to `logs\speed-trials.jsonl`. It records:
  - prefill tok/s, writing tok/s and time to first token
  - hit rate and PCIe share
  - peak GPU temperature and power
  - the live tuning and engine arguments at the time
  - The screen shows "TRIAL SAVED 17:22 · 47.9 TOK/S" and adds a point to the trials chart. No pop-up.
- **Trials chart**: every trial over the days, so you can see whether a tuning change made it faster or slower, A/B
  style.

**07 Ship's log.** The full log, with filters by source and level, a detail pane, the incident snapshots, and
warnings per hour.

**08 Reports**
- Buttons: LAST HOUR · THIS SHIFT · TODAY · SINCE STRATA STARTED · THIS INCIDENT.
- The report is **drawn on the screen**, with its charts.
- At the same moment it is saved quietly as an HTML file, with charts, plus a Markdown copy, in `logs\reports\`.
- An "open in browser" button is there if you want it, but nothing opens on its own.

**09 Maintenance**
- Counters: uptime, requests, tokens, cache reuse, compactions today.
- The log folder's size; a diagnostics bundle (saved quietly); always on top.
- The monitor's own health.

## 5. Warp coils and core colours

Strata reports where experts came from (the card's cache, PCIe, RAM or file) as totals for each finished request.
Nothing comes per layer or per expert. While a request runs, we only get its state, speed and PCIe MB/s. So:

- the coils are coloured by the last request's mix: blue-white from the cache, violet over PCIe, orange from RAM or
  file
- the coils move with the live state and speed
- lighting coils per expert would need a change in Strata, so it is not in this plan

| What's happening | Core colour |
|---|---|
| Idle | slow, dim blue |
| Reading the prompt | amber, flowing from the matter injector |
| Generating | bright blue, speed follows tok/s |
| Full re-read (big prompt, nothing reused) | gold |
| Speed trial running | green "test pattern" sweep |
| Standby (unloaded) | dark, no movement, with a "STANDBY" plate |
| Loading / powering up | rings light one by one from the ends inward |
| VRAM low or GPU hot | orange, and the field flickers |
| Error or red alert | red, pulsing |
| Offline | dark red |

## 6. The five decisions: my recommendations

1. **Keep the warp core on every screen.** Yes. It's the panel's identity, and it shows what the engine is doing
   while you work on another screen. On Core control and Diagnostics it shows the action live (powering down, the
   scan sweep).
2. **Test requests (speed trials).** Yes, as you want, with three guards:
   - They run only when Strata is idle.
   - They take two clicks, and the button says what it costs.
   - They are never run on a timer.

   The cost: Strata's conversation cache is off (0 MiB), so a trial pushes pi's conversation out of the engine's
   memory, and pi's next turn re-reads all of it. That is about 40–100 s at 90K tokens. Best done while pi is
   finished or between tasks. **Later option:** Strata's opt-in conversation cache (on hold since this morning) could
   park pi's conversation and bring it back after a trial. It costs VRAM, so it would need its own test.
3. **Controls.** Yes, as you want:
   - standby, engage, restart engine, full restart and shut down
   - live sliders for temperature, top_p, top_k, thinking and budget
   - sliders for the saved settings
   - each guarded by two clicks and the busy check

   The **engine arguments stay read-only** at first. Strata's own settings page doesn't edit them, and a wrong
   `--max-context` gives pi the 400 error again. If you want them later, the panel would edit them with a backup,
   then run a speed trial before and after.
4. **Names.** Keep the Trek names, with the plain name in small type under each one ("WARP CORE · the engine in
   depth"). That's fun, and still clear at 2 a.m.
5. **Reports.** Drawn on the screen and saved quietly as HTML with charts, plus Markdown. Nothing opens unless you
   click "open in browser".

**One rule changes.** Until now the monitor only read. With Core control it can change Strata's settings and
stop and start it, but only when you click, and every change is written down. It never changes anything by itself.

## 7. Phases

Each phase ends with:
- screenshots at 1280×780 and 1560×900
- `--selftest` output for each screen
- a clean `monitor.log`

Backups come first. Nothing is uploaded.

| Phase | Delivers |
|---|---|
| 1 | The framework: 9 department buttons and F1–F9, condition in the header, counters to Maintenance, nothing leaves the panel (bundle and logs open inside it) |
| 2 | Warp core screen and the new coil colours |
| 3 | **Core control**: the engine buttons, the live sliders, the saved settings, the tuning log |
| 4 | **Diagnostics**: levels 5–1, the scan sweep, speed trials and the trials chart |
| 5 | Power & coolant, Comms · pi link |
| 6 | Ship's log, Maintenance |
| 7 | Reports |

Phases 3 and 4 come early because they're the new abilities. Each control will be tried for real: a speed trial, a
standby/engage cycle, and a temperature change and back, while pi is idle.

`lcars_window.py` will be split, with each screen in its own file in `pages\`. It stays tkinter only, with nothing to
install, and `--classic` keeps working.
