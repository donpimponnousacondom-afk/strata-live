"""Talk to Strata from outside the console (for Claude, or any script) and have the conversation show live in the
console's 06 DIRECT LINE.

    python tools\\ask_strata.py "your message" [--effort none|low|medium|high] [--max 1024] [--new] [--as NAME]

It keeps its own conversation (logs\\chats\\<name>-conversation.json) so follow-up questions have context; --new
starts over. Every step is appended to logs\\chats\\feed.jsonl, which the Direct Line reads: the message, the thinking
and the answer as they stream (in small batches), then the numbers. The answer is also printed here.
Uses only the standard library; Strata answers one request at a time, as for any app."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHATS = os.path.join(ROOT, "logs", "chats")
FEED = os.path.join(CHATS, "feed.jsonl")
URL = os.environ.get("STRATA_URL", "http://127.0.0.1:8080").rstrip("/") + "/v1/chat/completions"


def feed(rec: dict) -> None:
    with open(FEED, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message")
    ap.add_argument("--file", help="add this text file's contents after the message (for long prompts)")
    ap.add_argument("--effort", default="none", choices=["none", "low", "medium", "high", "config"])
    ap.add_argument("--max", type=int, default=1024)
    ap.add_argument("--new", action="store_true", help="start a new conversation")
    ap.add_argument("--as", dest="who", default="claude", help="the name shown in the Direct Line")
    a = ap.parse_args()
    os.makedirs(CHATS, exist_ok=True)
    conv_path = os.path.join(CHATS, f"{a.who}-conversation.json")
    msgs = []
    if not a.new and os.path.isfile(conv_path):
        try:
            msgs = json.load(open(conv_path, encoding="utf-8"))
        except ValueError:
            msgs = []
    content = a.message
    if a.file:
        content += "\n\n" + open(a.file, encoding="utf-8").read()
    msgs.append({"role": "user", "content": content})
    cid = uuid.uuid4().hex[:8]
    feed({"id": cid, "ts": time.time(), "who": a.who, "type": "user",
          "text": a.message + (f"  [+ {os.path.basename(a.file)}, {len(content) - len(a.message):,} characters]"
                               if a.file else ""), "new": a.new,
          "turn": len(msgs) // 2 + 1})
    body = {"model": "strata", "messages": msgs, "stream": True, "max_tokens": a.max,
            "stream_options": {"include_usage": True}}
    if a.effort != "config":
        body["reasoning_effort"] = a.effort
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    first = None
    answer, thinking, buf, usage, finish = [], [], {"think": "", "answer": ""}, None, None
    last_flush = time.time()

    def flush(force=False):
        nonlocal last_flush
        if force or time.time() - last_flush > 0.25:
            for k in ("think", "answer"):
                if buf[k]:
                    feed({"id": cid, "ts": time.time(), "who": a.who, "type": k, "text": buf[k]})
                    buf[k] = ""
            last_flush = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    d = json.loads(line[5:])
                except ValueError:
                    continue
                if d.get("usage"):
                    usage = d["usage"]
                ch = (d.get("choices") or [{}])[0] if d.get("choices") else {}
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                delta = ch.get("delta") or {}
                for key, kind, sink in (("reasoning_content", "think", thinking), ("content", "answer", answer)):
                    if delta.get(key):
                        first = first or time.time()
                        sink.append(delta[key])
                        buf[kind] += delta[key]
                flush()
    except Exception as e:
        flush(True)
        feed({"id": cid, "ts": time.time(), "who": a.who, "type": "error", "text": str(e)})
        print(f"error: {e}", file=sys.stderr)
        return 1
    flush(True)
    t1 = time.time()
    out = (usage or {}).get("completion_tokens") or 0
    stats = {"tokens": out, "tok_s": round(out / (t1 - first), 1) if first and t1 - first > 0.2 else None,
             "ttft": round(first - t0, 2) if first else None, "prompt": (usage or {}).get("prompt_tokens"),
             "finish": finish, "wall": round(t1 - t0, 1)}
    feed({"id": cid, "ts": t1, "who": a.who, "type": "done", "stats": stats})
    text = "".join(answer)
    msgs.append({"role": "assistant", "content": text})
    json.dump(msgs, open(conv_path, "w", encoding="utf-8"), indent=1)
    print(text.strip())
    print(f"\n({out} tokens, {stats['tok_s']} tok/s, first token {stats['ttft']} s, ended: {finish})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
