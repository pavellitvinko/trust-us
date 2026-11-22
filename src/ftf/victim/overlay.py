"""What the agent is doing, drawn on the page for whoever watches or records the run.

A recording of the browser shows no mouse pointer and no click, so before each action the
page gets a caption bar (step, action, reason) and an outline around the element about to be
used. Both live in one element appended to `<html>` (outside `<body>`) with a closed shadow
root, and that element is removed before every observation, screenshot and DOM snapshot
(`Recorder.before_capture`), so the recorded evidence is the same with or without it.

The same captions are kept as cues with their time in the video, and written as WebVTT next
to each clip (`run_files/recording/clip-NN.vtt`).
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from .observe import Candidate

HOST_ID = "__ftf_victim_overlay"

SHOW_JS = r"""
(a) => {
  const ID = '__HOST_ID__';
  let host = document.getElementById(ID);
  if (!host) {
    host = document.createElement('ftf-overlay');
    host.id = ID;
    host.setAttribute('style', 'all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147483647');
    const root = host.attachShadow({ mode: 'closed' });
    root.innerHTML = `<style>
      :host{all:initial}
      .tag{position:fixed;top:10px;left:10px;font:600 12px/1.2 system-ui,sans-serif;color:#fff;
        background:rgba(20,24,32,.82);padding:6px 10px;border-radius:999px;letter-spacing:.2px}
      .tag b{color:#ffb547}
      .bar{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);max-width:min(92vw,980px);
        display:flex;gap:12px;align-items:flex-start;background:rgba(20,24,32,.9);color:#fff;
        padding:12px 16px;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.35);
        font:15px/1.35 system-ui,sans-serif}
      .step{flex:none;background:#ffb547;color:#1a1300;font-weight:700;border-radius:8px;padding:2px 8px;font-size:13px;margin-top:1px}
      .main{font-weight:600}
      .sub{color:#c9d1dc;font-size:13px;margin-top:2px}
      .box{position:fixed;border:3px solid #ffb547;border-radius:8px;box-shadow:0 0 0 4px rgba(255,181,71,.25),0 0 24px rgba(255,181,71,.55);transition:all .25s}
      .dot{position:fixed;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgba(255,181,71,.95);
        box-shadow:0 0 0 0 rgba(255,181,71,.7);animation:p 1s ease-out infinite}
      @keyframes p{0%{box-shadow:0 0 0 0 rgba(255,181,71,.7)}100%{box-shadow:0 0 0 22px rgba(255,181,71,0)}}
      [hidden]{display:none!important}
    </style>
    <div class="tag">Follower · <b class="who"></b></div>
    <div class="box" hidden></div><div class="dot" hidden></div>
    <div class="bar"><span class="step"></span><div><div class="main"></div><div class="sub"></div></div></div>`;
    host.__root = root;
    document.documentElement.appendChild(host);
  }
  const root = host.__root;
  const q = (s) => root.querySelector(s);
  q('.who').textContent = a.who || '';
  q('.step').textContent = a.badge || '';
  q('.step').hidden = !a.badge;
  q('.main').textContent = a.caption || '';
  q('.sub').textContent = a.sub || '';
  q('.sub').hidden = !a.sub;
  const box = q('.box'), dot = q('.dot');
  box.hidden = dot.hidden = true;
  if (a.xpath) {
    let el = null;
    try { el = document.evaluate(a.xpath, document, null, 9, null).singleNodeValue; } catch (e) {}
    if (el && el.getBoundingClientRect) {
      const r = el.getBoundingClientRect();
      if (r.width || r.height) {
        const pad = 4;
        Object.assign(box.style, { left: (r.left - pad) + 'px', top: (r.top - pad) + 'px',
          width: (r.width + 2 * pad) + 'px', height: (r.height + 2 * pad) + 'px' });
        box.hidden = false;
        if (a.dot) { Object.assign(dot.style, { left: (r.left + r.width / 2) + 'px', top: (r.top + r.height / 2) + 'px' }); dot.hidden = false; }
        // Keep the bar off the element it points at.
        const bar = q('.bar');
        bar.style.bottom = r.bottom > innerHeight - 120 ? '' : '18px';
        bar.style.top = r.bottom > innerHeight - 120 ? '18px' : '';
      }
    }
  }
  return true;
}
""".replace("__HOST_ID__", HOST_ID)

UNPOINT_JS = f"""() => {{ const h = document.getElementById('{HOST_ID}');
  if (h && h.__root) h.__root.querySelectorAll('.box, .dot').forEach(e => e.hidden = true); return true; }}"""
CLEAR_JS = f"""() => {{ const h = document.getElementById('{HOST_ID}'); if (h) h.remove(); return true; }}"""


def _short(text: str, n: int = 70) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def action_caption(c: Candidate, typed: str | None = None) -> str:
    """The chosen action in plain words, for the caption bar."""
    t = _short(c.text, 60)
    return {
        "link": f"Clicks the link “{t}”",
        "button": f"Clicks “{t}”",
        "close": f"Closes the pop-up (“{t}”)",
        "chat_option": f"Answers the chat: “{t}”",
        "text_input": f"Types into the chat: “{_short(typed or '', 60)}”",
        "media": "Plays the video for a while",
        "form": "Fills in the form with the persona’s details and sends it",
        "scroll": "Scrolls down and reads",
    }.get(c.kind, t)


@dataclass
class Cue:
    clip: int
    start: float  # seconds since the clip started
    text: str


@dataclass
class Overlay:
    """Draws on the run's page and keeps the caption cues. `draw=False` keeps only the cues."""

    who: str
    draw: bool = True
    hold_s: float = 0.0  # how long the highlight stays before the action
    cues: list[Cue] = field(default_factory=list)
    clip_started: dict[int, tuple[int, float]] = field(
        default_factory=dict
    )  # id(page) -> (clip, t0)

    def start_clip(self, page: Any, at: float | None = None) -> None:
        if id(page) not in self.clip_started:
            self.clip_started[id(page)] = (len(self.clip_started) + 1, at or time.monotonic())

    def show(
        self,
        page: Any,
        caption: str,
        sub: str = "",
        badge: str = "",
        xpath: str = "",
        dot: bool = False,
        hold: float | None = None,
    ) -> None:
        clip, t0 = self.clip_started.get(id(page), (0, 0.0))
        if clip:
            text = f"{badge} · {caption}" if badge else caption
            self.cues.append(
                Cue(clip, max(0.0, time.monotonic() - t0), text + (f"\n{sub}" if sub else ""))
            )
        if self.draw:
            with contextlib.suppress(Exception):  # the page may be navigating
                page.evaluate(
                    SHOW_JS,
                    {
                        "who": self.who,
                        "caption": caption,
                        "sub": sub,
                        "badge": badge,
                        "xpath": xpath,
                        "dot": dot,
                    },
                )
        wait = self.hold_s if hold is None else hold
        if wait > 0:
            with contextlib.suppress(Exception):
                page.wait_for_timeout(int(wait * 1000))

    def unpoint(self, page: Any) -> None:
        """After the action: the outline would mark where the element was, so it goes."""
        if self.draw and page is not None:
            with contextlib.suppress(Exception):
                page.evaluate(UNPOINT_JS)

    def clear(self, page: Any) -> None:
        if self.draw and page is not None:
            with contextlib.suppress(Exception):
                page.evaluate(CLEAR_JS)

    def vtt(self, clip: int, duration: float | None = None) -> str:
        """WebVTT for one clip: each cue lasts until the next one (or 4 s for the last)."""
        cues: list[Cue] = []
        for c in self.cues:  # the same caption shown twice in a row is one cue
            if c.clip == clip and not (cues and cues[-1].text == c.text):
                cues.append(c)
        lines = ["WEBVTT", ""]
        for i, cue in enumerate(cues):
            end = cues[i + 1].start if i + 1 < len(cues) else cue.start + 4.0
            if duration is not None:
                end = min(end, duration) if end > cue.start else end
            end = max(end, cue.start + 0.5)
            lines += [str(i + 1), f"{_ts(cue.start)} --> {_ts(end)}", cue.text, ""]
        return "\n".join(lines)


def _ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
