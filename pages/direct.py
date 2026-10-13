"""06 DIRECT LINE: talk to Strata from the console — no pi. The reply streams in with its thinking, and the speed is
measured as it arrives. Strata's own chat page is not touched; this uses Strata's API, as pi does.

Strata answers one request at a time and keeps one conversation in memory (the conversation cache is off), so a
message here makes pi re-read its conversation on its next turn. Each finished exchange is saved quietly to
logs\\chats\\chat-<date>.jsonl."""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.request

import strata_control as ctl
from lcars_theme import BG, BLUE, GOLD, GREEN, GRID, INK, LILAC, MUTED, ORANGE, PEACH, RED_HI, SKY, TAN, VIOLET
from pages.base import Page

CHAT_DIR = os.path.join(ctl.sl.LOG_DIR, "chats")
FEED = os.path.join(CHAT_DIR, "feed.jsonl")         # conversations from outside: tools\ask_strata.py


class DirectPage(Page):
    code, title, sub, color = "06", "DIRECT LINE", "talk to strata", GOLD

    def __init__(self, w):
        super().__init__(w)
        self.messages: list = []            # the conversation sent to Strata: [{"role", "content"}]
        self.q: queue.Queue = queue.Queue()
        self.streaming = False
        self.stop_flag = False
        self.effort = "none"
        self.max_tokens = 4096
        self.stats: dict = {}
        self.view = self.entry = None
        self.polling = False

    # ------------------------------------------------------------ widgets (made once, placed on each build)
    def make_widgets(self) -> None:
        w = self.w
        self.view = tk.Text(w.root, bg="#05050a", fg=INK, font=w.f_small, wrap="word", relief="flat", bd=0,
                            highlightthickness=1, highlightbackground=GRID, padx=10, pady=8, cursor="arrow",
                            insertbackground=BG, selectbackground="#3a2f55")
        self.view.tag_configure("you", foreground=GOLD, font=w.f_lab, spacing1=10)
        self.view.tag_configure("you_text", foreground=TAN)
        self.view.tag_configure("strata", foreground=SKY, font=w.f_lab, spacing1=10)
        self.view.tag_configure("think", foreground=MUTED, font=w.f_tiny, lmargin1=14, lmargin2=14)
        self.view.tag_configure("answer", foreground=INK)
        self.view.tag_configure("meta", foreground=LILAC, font=w.f_tiny)
        self.view.tag_configure("error", foreground=RED_HI)
        self.view.tag_configure("other", foreground=VIOLET, font=w.f_lab, spacing1=10)
        self.view.tag_configure("other_text", foreground=LILAC)
        self.view.tag_configure("strata_other", foreground=SKY, font=w.f_lab, spacing1=6)
        self.view.configure(state="disabled")
        self.entry = tk.Text(w.root, bg="#0b0b10", fg=INK, font=w.f_small, wrap="word", relief="flat", height=3,
                             highlightthickness=2, highlightbackground=GOLD, highlightcolor=ORANGE,
                             insertbackground=ORANGE, padx=8, pady=6)
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Shift-Return>", lambda e: None)
        self.intro()
        self.feed_pos = 0
        self.feed_state: dict = {}
        self.read_feed(since=time.time() - 4 * 3600)      # the last few hours of outside conversations

    # ------------------------------------------------------------ conversations from outside (tools\ask_strata.py)
    def read_feed(self, since: float | None = None) -> None:
        """Show what others (Claude, scripts) say to Strata: logs\\chats\\feed.jsonl, written as it streams."""
        try:
            size = os.path.getsize(FEED)
        except OSError:
            return
        if size < self.feed_pos:
            self.feed_pos = 0
        if size == self.feed_pos:
            return
        with open(FEED, encoding="utf-8", errors="replace") as f:
            f.seek(self.feed_pos)
            data = f.read()
            self.feed_pos = f.tell()
        got = False
        for line in data.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if since and r.get("ts", 0) < since:
                continue
            self.show_feed(r)
            got = True
        if got and since is None and self.w.page != self.w.pages.index(self):
            self.w.dept_alert[self.w.pages.index(self)] = True    # the button blinks: a conversation came in

    def show_feed(self, r: dict) -> None:
        who = (r.get("who") or "other").upper()
        st = self.feed_state.setdefault(r.get("id"), {"head": False, "think": False})
        t = r.get("type")
        if t == "user":
            if r.get("new"):
                self.write(f"\n— {who} STARTED A NEW CONVERSATION —\n", "meta")
            self.write(f"\n{who} (VIA API) · {dt.datetime.fromtimestamp(r['ts']):%H:%M:%S}\n", "other")
            self.write(r.get("text", "") + "\n", "other_text")
        elif t in ("think", "answer"):
            if not st["head"]:
                st["head"] = True
                self.write(f"STRATA → {who}\n", "strata_other")
            if t == "think":
                if not st["think"]:
                    st["think"] = True
                    self.write("thinking · ", "think")
                self.write(r.get("text", ""), "think")
            else:
                if st["think"]:
                    st["think"] = False
                    self.write("\n", "think")
                self.write(r.get("text", ""), "answer")
        elif t == "done":
            s = r.get("stats") or {}
            bits = [f"{s.get('tokens', 0):,} tokens"]
            if s.get("tok_s"):
                bits.append(f"{s['tok_s']} tok/s")
            if s.get("ttft") is not None:
                bits.append(f"first token {s['ttft']} s")
            bits.append(f"ended: {s.get('finish') or '?'}")
            self.write("\n" + " · ".join(bits) + "\n", "meta")
        elif t == "error":
            self.write(f"\n[{who}'s line dropped: {r.get('text')}]\n", "error")

    def intro(self) -> None:
        self.write("DIRECT LINE TO STRATA · NO PI\n", "meta")
        self.write("Type below and press Enter (Shift+Enter for a new line). Thinking shows in grey as it arrives.\n"
                   "Strata keeps one conversation in memory, so pi re-reads its own on its next turn.\n"
                   "Conversations from outside (Claude, through tools\\ask_strata.py) show here too, in violet.\n",
                   "meta")

    def build(self, area) -> None:
        super().build(area)
        if self.view is None:
            self.make_widgets()
        x0, y0, x1, y1 = area
        top = y0 + 64
        self.entry_h = 70
        self.c.create_window(x0, top, anchor="nw", window=self.view, width=x1 - x0, height=y1 - top - self.entry_h - 40,
                             tags=("static",))
        self.c.create_window(x0, y1 - self.entry_h, anchor="nw", window=self.entry, width=x1 - x0 - 110,
                             height=self.entry_h, tags=("static",))
        self.entry.focus_set()
        say = next((x.split("=", 1)[1] for x in sys.argv if x.startswith("--say=")), None)
        if say and not getattr(self, "said", False):          # testing: send one message on its own
            self.said = True
            self.entry.insert("1.0", say)
            self.w.root.after(1500, self.send)
        if not self.polling:
            self.polling = True
            self.w.root.after(80, self.poll)

    # ------------------------------------------------------------ text
    def write(self, s: str, tag: str) -> None:
        if not self.view:
            return
        self.view.configure(state="normal")
        at_end = self.view.yview()[1] > 0.98
        self.view.insert("end", s, tag)
        self.view.configure(state="disabled")
        if at_end:
            self.view.see("end")

    # ------------------------------------------------------------ sending
    def on_enter(self, e):
        self.send()
        return "break"

    def send(self) -> None:
        text = self.entry.get("1.0", "end").strip()
        if not text or self.streaming:
            return
        self.entry.delete("1.0", "end")
        self.messages.append({"role": "user", "content": text})
        self.write(f"\nYOU · {dt.datetime.now():%H:%M:%S}\n", "you")
        self.write(text + "\n", "you_text")
        self.write(f"\nSTRATA\n", "strata")
        self.streaming, self.stop_flag = True, False
        self.stats = {"t0": time.time(), "first": None, "chunks": 0, "think_open": False}
        threading.Thread(target=self.stream, args=(list(self.messages), self.effort, self.max_tokens),
                         daemon=True).start()

    def stream(self, messages, effort, max_tokens) -> None:
        body = {"model": "strata", "messages": messages, "stream": True, "max_tokens": max_tokens,
                "stream_options": {"include_usage": True}}
        if effort != "config":
            body["reasoning_effort"] = effort
        req = urllib.request.Request(ctl._url("/v1/chat/completions"), data=json.dumps(body).encode(), method="POST",
                                     headers=ctl._headers())
        answer, thinking, usage, finish = [], [], None, None
        try:
            with urllib.request.urlopen(req, timeout=1800) as r:
                for raw in r:
                    if self.stop_flag:
                        finish = "stopped"
                        break
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
                    delta = ch.get("delta") or {}
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    if delta.get("reasoning_content"):
                        thinking.append(delta["reasoning_content"])
                        self.q.put(("think", delta["reasoning_content"]))
                    if delta.get("content"):
                        answer.append(delta["content"])
                        self.q.put(("answer", delta["content"]))
        except Exception as e:
            self.q.put(("error", f"\n[the line dropped: {e}]\n"))
            finish = "error"
        self.q.put(("done", {"answer": "".join(answer), "thinking": "".join(thinking), "usage": usage,
                             "finish": finish}))

    def poll(self) -> None:
        """Move streamed text from the worker thread into the view (tk must only be touched from its own thread)."""
        try:
            while True:
                kind, val = self.q.get_nowait()
                st = self.stats
                if kind in ("think", "answer") and st.get("first") is None:
                    st["first"] = time.time()
                if kind == "think":
                    st["chunks"] += 1
                    if not st.get("think_open"):
                        st["think_open"] = True
                        self.write("thinking · ", "think")
                    self.write(val, "think")
                elif kind == "answer":
                    st["chunks"] += 1
                    if st.get("think_open"):
                        st["think_open"] = False
                        self.write("\n", "think")
                    self.write(val, "answer")
                elif kind == "error":
                    self.write(val, "error")
                elif kind == "done":
                    self.finish(val)
        except queue.Empty:
            pass
        try:
            self.read_feed()
        except Exception:
            import traceback
            print(traceback.format_exc(), flush=True)
        self.w.root.after(80, self.poll)

    def finish(self, res: dict) -> None:
        st = self.stats
        t1 = time.time()
        u = res.get("usage") or {}
        out = u.get("completion_tokens") or st.get("chunks") or 0
        gen_s = t1 - (st.get("first") or t1)
        ttft = (st["first"] - st["t0"]) if st.get("first") else None
        tok_s = out / gen_s if gen_s > 0.2 else None
        st.update(out=out, ttft=ttft, tok_s=tok_s, prompt=u.get("prompt_tokens"), finish=res.get("finish"),
                  cached=((u.get("prompt_tokens_details") or {}).get("cached_tokens")), wall=t1 - st["t0"])
        if res.get("answer"):
            self.messages.append({"role": "assistant", "content": res["answer"]})
        bits = [f"{out:,} tokens"]
        if tok_s:
            bits.append(f"{tok_s:.1f} tok/s")
        if ttft is not None:
            bits.append(f"first token {ttft:.1f} s")
        if st.get("prompt"):
            bits.append(f"prompt {st['prompt']:,}")
        bits.append(f"ended: {res.get('finish') or '?'}")
        self.write("\n" + " · ".join(bits) + "\n", "meta")
        self.streaming = False
        try:
            os.makedirs(CHAT_DIR, exist_ok=True)
            with open(os.path.join(CHAT_DIR, f"chat-{dt.date.today():%Y-%m-%d}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "effort": self.effort, "question": self.messages[-2]["content"]
                                    if len(self.messages) >= 2 else None, "answer": res.get("answer"),
                                    "thinking": res.get("thinking"), "stats": {k: v for k, v in st.items()
                                                                               if k not in ("think_open",)}}) + "\n")
        except (OSError, IndexError):
            pass

    def clear(self) -> None:
        if self.streaming:
            return
        self.messages = []
        self.view.configure(state="normal")
        self.view.delete("1.0", "end")
        self.view.configure(state="disabled")
        self.intro()

    # ------------------------------------------------------------ drawing (the controls over the view)
    def draw(self, s) -> None:
        w = self.w
        x0, y0, x1, y1 = self.area
        # thinking level for this chat
        w.text(x0, y0 + 8, "THINKING", w.f_lab, GOLD)
        opts = ("none", "low", "medium", "high", "config")
        bx = x0 + 74
        for o in opts:
            bw = 64 if o != "config" else 84
            w.button(bx, y0, bx + bw, y0 + 18, o.upper() if o != "config" else "STRATA'S", SKY,
                     lambda o=o: setattr(self, "effort", o), key="deff:" + o, active=self.effort == o, font=w.f_tiny)
            bx += bw + 5
        w.slider("dmax", bx + 14, y0 - 6, x1, "REPLY LIMIT", 256, 32768, 256, self.max_tokens, PEACH,
                 lambda v: setattr(self, "max_tokens", int(v)), fmt="{:,}")
        # status
        st = self.stats
        live = s.get("live") or {}
        if self.streaming:
            el = time.time() - st.get("t0", time.time())
            msg = (f"STRATA IS {str(live.get('state') or 'working').upper()} · {el:.0f} S"
                   + (f" · {live.get('tok_s'):.1f} TOK/S" if live.get("tok_s") else "")
                   + (" · WAITING IN LINE BEHIND ANOTHER REQUEST" if live.get("queued") else ""))
            col = GOLD
        elif st.get("finish"):
            msg = (f"LAST REPLY {st.get('out', 0):,} TOKENS" + (f" · {st['tok_s']:.1f} TOK/S" if st.get("tok_s") else "")
                   + (f" · FIRST TOKEN {st['ttft']:.1f} S" if st.get("ttft") is not None else "")
                   + f" · {len(self.messages) // 2} EXCHANGE(S) · SAVED TO LOGS\\CHATS")
            col = GREEN
        else:
            busy = ctl.busy()
            msg, col = (("STRATA IS BUSY: " + busy.upper() + " · YOUR MESSAGE WILL WAIT ITS TURN") if busy else
                        "READY · ONE REQUEST AT A TIME · PI RE-READS ITS CONVERSATION AFTER A CHAT HERE"), \
                       (GOLD if busy else MUTED)
        w.text(x0, y0 + 40, w.fit(msg, w.f_tiny, x1 - x0), w.f_tiny, col)
        # buttons by the entry box
        ex = x1 - 100
        ey = y1 - self.entry_h if hasattr(self, "entry_h") else y1 - 70
        if self.streaming:
            w.button(ex, ey, x1, ey + 32, "STOP", RED_HI, lambda: setattr(self, "stop_flag", True), key="dstop")
        else:
            w.button(ex, ey, x1, ey + 32, "SEND", GOLD, self.send, key="dsend", enabled=s.get("up", False))
        w.button(ex, ey + 38, x1, ey + 70, "CLEAR", LILAC, self.clear, key="dclear", enabled=not self.streaming)
        w.text(x0, y1 - self.entry_h - 18 if hasattr(self, "entry_h") else y1 - 88,
               f"CONVERSATION: {len(self.messages)} MESSAGE(S) · ENTER SENDS · SHIFT+ENTER NEW LINE", w.f_tiny, MUTED)
