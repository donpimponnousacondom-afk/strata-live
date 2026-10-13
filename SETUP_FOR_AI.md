# Strata Live: setup guide for an AI assistant

This file tells an AI coding assistant (Claude Code, Codex, Cursor, Copilot, ...) how to install **Strata Live** on a
user's PC, and how to use it to troubleshoot. Strata Live is a small, read-only monitor for a
[Strata](https://github.com/Niko1221/Strata) install. It shows a live page of what Strata is doing, logs timeouts,
context errors and cut-off replies, records incidents, tracks speed and settings changes, and can make a diagnostics
bundle.

## The one rule: change nothing in Strata

Strata Live only **reads**. Never edit, move or delete anything in the user's Strata folder, the model data folder,
or the user's AI app (such as pi). Do not patch Strata's code or its `strata-<model>.json` configs to make the monitor
work. Everything Strata Live needs goes in its own folder.

The one exception is the optional relay (see below): using it means pointing the user's app at a different port.
Only do that if the user asks, tell them exactly what you changed, and change it back when they're done.

## What it reads and writes

| | Path | Access |
| --- | --- | --- |
| Strata's status page | `http://127.0.0.1:8080/metrics` | read, once a second |
| Strata's log files | `<strata folder>/strata-*.log` | read new lines only |
| Strata's settings | `<strata folder>/strata-*.json`, `<strata folder>/engine/BUILD.json` | read every 10 s, to spot changes |
| pi's session files (optional) | `~/.pi/agent/sessions/*/*.jsonl` | read new lines only |
| pi's settings (optional) | `~/.pi/agent/models.json`, `~/.pi/agent/settings.json` | read every 10 s, to spot changes |
| Windows' System event log | via `wevtutil` (Windows only) | read once a minute |
| Its own logs | `<strata-live folder>/logs/` | write |
| Its own web page | `http://127.0.0.1:8099/` | serves, on this PC only |
| Relay (off by default) | `http://127.0.0.1:8090/` → Strata | passes requests through unchanged |

It never reads pi's `auth.json`. Settings are stored with secrets replaced by `***`.

It needs **Python 3.9 or newer, standard library only**. Nothing is installed with pip. Strata's own Python
(`<strata folder>/.venv`) works, and using it changes nothing in Strata.

## Files

Copy these files into a new folder outside the Strata folder, for example `D:\strata-live` or `~/strata-live`:

- `strata_live.py`: the monitor, relay and small web server
- `live.html`: the live page it serves
- `start-strata-live.bat`: Windows launcher (edit its paths, see step 3)
- `start-strata-live-with-relay.bat`: the same with the relay on port 8090

Don't copy a `logs/` folder from another PC: it holds that user's events.

## Setup steps

### 1. Check that Strata is running and reachable

```
curl -s -H "Accept: application/json" http://127.0.0.1:8080/metrics
```

A JSON reply with `"engine"`, `"live"` and `"hardware"` keys means it works. If Strata uses another port, note the
URL. If the reply is a 401 or similar, Strata runs with `--api-key`. Ask the user for the key and pass it as
`STRATA_API_KEY` (step 3). Never print the key in chat or write it into shared files.

### 2. Find the Strata folder

It's the folder with `serve/server.py`, `setup.py` and the `strata-<model>.log` files. Common places are
`D:\strata` or `C:\strata` on Windows and `~/strata` on Linux.

### 3. Set the settings

The monitor uses environment variables. Only set the ones that differ from the defaults:

| Variable | Default | Meaning |
| --- | --- | --- |
| `STRATA_URL` | `http://127.0.0.1:8080` | where Strata's server answers |
| `STRATA_DIR` | `D:\strata` | the Strata folder (logs and settings) |
| `STRATA_LIVE_PORT` | `8099` | the port of the live page |
| `STRATA_RELAY_PORT` | (off) | turns the relay on at this port |
| `STRATA_API_KEY` | (empty) | only if Strata was started with `--api-key` |

**Windows:** edit both `.bat` files. Point them at a Python that exists, and add `set` lines before the `python`
line when a setting differs:

```bat
@echo off
title Strata Live (read-only monitor)
cd /d "%~dp0"
set STRATA_DIR=C:\path\to\strata
rem set STRATA_URL=http://127.0.0.1:8080
"C:\path\to\strata\.venv\Scripts\python.exe" "%~dp0strata_live.py"
if errorlevel 1 pause
```

**Linux / macOS:**

```sh
cd ~/strata-live
STRATA_DIR=~/strata python3 strata_live.py
```

### 4. Start it and verify

Start the launcher (or the command above). The page opens in the browser by itself. Add `--no-browser` to skip
that. Then check:

```
curl -s http://127.0.0.1:8099/api/state
```

Check that:
- `"up": true` means it reaches Strata.
- `"events"` contains a `started` event that names how many Strata logs and pi session files it found, and a
  `config tracking` event on the first run.
- `"speed"` has entries when Strata's logs had requests (the history is rebuilt from them on the first run).
- `"windows"` is `"ok"` on Windows.
- `logs/events-<date>.log` exists in the Strata Live folder.
- `git status` in the Strata folder shows nothing new from you. This proves nothing in Strata was changed.

Tell the user the page address and where the logs are. Tell them to close the window, or press Ctrl+C, to stop it.
Starting it twice only opens the page of the copy that's already running.

## Troubleshooting with it

When a user reports a problem, look in this order:

1. **`logs/incidents/`**: each file holds one snapshot per second from 5 minutes before to 1 minute after a problem,
   plus the events that triggered it. Read the `trigger`, `events` and the `samples[].live` / `samples[].hw` fields
   around `ts`.
2. **`logs/events-<date>.log`**: every event in plain words, including the Strata request each pi error matches.
3. **Changes & system** (on the page, or `config changed` / `windows` lines in the log): a settings change or a
   driver reset often explains a sudden change in behaviour.
4. **`logs/speed-history.json`**: whether a model got slower on a given day.
5. If the cause is still unclear, ask the user to click **Make diagnostics bundle** and share the zip from
   `logs/bundles/`. It leaves out pi's session files (their conversations) and hides secrets.

### Reading a pi timeout

pi's "Request timed out." doesn't always mean Strata was slow. The `timeout` event says how many seconds after
sending the request pi reported it, and whether Strata had a request open at that moment:

- **reported within a few seconds and Strata had no request open:** the request failed on pi's side and never
  reached Strata. pi usually retries by itself.
- **Strata had a request open:** read the incident recording to see what Strata was doing (a long thinking phase, a
  huge tool call, a queue).

To be sure, use the relay: if a request pi says timed out never appears in the Relay panel or `logs/relay-<date>.log`,
it never left pi.

### Using the relay (only if the user agrees)

1. Start `start-strata-live-with-relay.bat` (or set `STRATA_RELAY_PORT=8090`).
2. In the app's settings, change the Strata address from port 8080 to 8090. For pi that's `baseUrl` in
   `~/.pi/agent/models.json` (`http://127.0.0.1:8090/v1`). Back the file up first.
3. Each relayed request is logged with: when Strata answered, when the first data arrived, the longest silence and
   how it ended (`done`, `app hung up`, or a connection error). A `relay silence` event fires at 60 s and 240 s
   without data.
4. When done, restore the app's original address.

## What it reports

| Event | Meaning |
| --- | --- |
| `timeout` (pi) | pi reported "Request timed out.": how long after sending, what Strata was doing, and whether Strata had a request open (see above). |
| `context too long` (pi) | Strata refused a request because prompt + `max_tokens` did not fit its context. Strata never cuts a prompt by itself. |
| `reply cut at limit` | a reply stopped at the app's `max_tokens` (pi: "Response was truncated before completion."). Says if it was all thinking with no answer (fix: `reasoning_budget_tokens` in `strata-<model>.json`, which is the user's decision) or a huge tool call. |
| `pi compacted` | pi summarised the conversation because it neared the context limit. |
| `app hung up` | the app closed the connection before the reply finished. |
| `conversation shortened` | the prompt suddenly got much smaller: the app compacted or restarted the conversation. |
| `near context limit` | a request used more than 85% of Strata's context. |
| `queue` / `long request` | requests waited over 2 minutes, or one ran for over 10 minutes. |
| `runaway reply` | one reply passed 8,000 tokens and is still going. |
| `slow tool call` | one tool call has been written for over 5 minutes. |
| `vram low`, `ram full`, `gpu hot` | under 250 MiB VRAM free, RAM over 95%, or GPU at 85 °C or more, each for 10 s. |
| `slowdown` | a model's median speed today (10+ replies) is over 25% below its previous days. |
| `relay silence` | a relayed request had no data from Strata for 60 s or 240 s. |
| `graphics driver reset`, `graphics driver error`, `hardware error`, `unexpected shutdown`, `windows low memory` | from Windows' System event log. |
| `config changed` | a watched settings file changed, with what changed. Changes made while the monitor was off are reported at its next start. |
| `log error`, `warning` | a matching line appeared in a Strata log. Strata's harmless `VirtualAlloc error 1314` line (large pages refused) is ignored. |
| `server` | Strata stopped answering (3 missed checks in a row, so a busy Strata isn't reported as down) or came back. |

Errors, and the runaway, slow tool call, VRAM, RAM, temperature, relay silence, Windows low memory and server
warnings, also save an incident recording. The page also shows pi errors and Windows events from the last 48 hours
when it starts.

## Notes and limits

- **pi is optional.** Without pi's folder, the pi events are simply missing. Other apps' own logs are not read.
- **The pi compaction marker** assumes pi's default `compaction.reserveTokens` of 16,384. If the user changed it, edit
  `PI_COMPACT_RESERVE` in `strata_live.py`.
- **Warning levels** are constants at the top of `strata_live.py`. On a card that normally runs with little free
  VRAM, lower `VRAM_LOW_MIB` if `vram low` fires all the time.
- **Housekeeping:** event logs, relay logs and incidents older than 30 days and bundles older than 14 days are
  deleted. `events.jsonl` is trimmed past 20 MB.
- **The page, relay and bundle download are only reachable from this PC** (`127.0.0.1`). Don't change them to listen
  on the network: they show what Strata is working on.
- **Tested on:** Strata engine 0.1.41 on Windows 11, with pi 1.1.0, an RTX 3060 and 56 GB of RAM. Linux should
  work but was not tested; the Windows event part is Windows only.
