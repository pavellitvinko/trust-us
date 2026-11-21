"""Observation (VV-11, VV-12): the page as a numbered list of things the visitor can do.

A script in the page lists links, buttons, chat options, forms, media and close buttons, with
what the rules need to judge them. It never changes the DOM, so the saved page is what the
site served; elements are found again by XPath. Every frame is also scanned for the flags
that stop a run (payment fields, file inputs, password fields, CAPTCHAs).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

MAX_CANDIDATES = 60
MAX_TEXT = 20_000

# Runs in the page. Returns plain JSON; touches nothing.
OBSERVE_JS = r"""
(maxText) => {
  const vis = (el) => {
    if (!el || !el.getClientRects || el.getClientRects().length === 0) return false;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true })) return false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };
  const xpath = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
      let i = 1;
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling)
        if (s.nodeName === n.nodeName) i++;
      parts.unshift(n.nodeName.toLowerCase() + '[' + i + ']');
    }
    return '/' + parts.join('/');
  };
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const label = (el) => {
    let t = '';
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = l.innerText; }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    return clean(t || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.title || '');
  };
  const inView = (el) => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };
  const marks = (el) => clean(((el.className && el.className.baseVal !== undefined) ? el.className.baseVal : el.className) + ' ' + (el.id || ''));
  const ancestorMarks = (el) => { let t = ''; for (let n = el, k = 0; n && k < 6; n = n.parentElement, k++) t += ' ' + marks(n); return t.toLowerCase(); };
  const fieldInfo = (el) => ({
    tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || el.tagName).toLowerCase(),
    name: el.getAttribute('name') || '', id: el.id || '', label: label(el),
    autocomplete: el.getAttribute('autocomplete') || '', required: el.required || el.getAttribute('aria-required') === 'true',
    pattern: el.getAttribute('pattern') || '', inputmode: el.getAttribute('inputmode') || '',
    maxlength: el.maxLength > 0 ? el.maxLength : null, visible: vis(el), xpath: xpath(el),
    options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 30).map(o => clean(o.text)) : [],
    accept: el.getAttribute('accept') || '', capture: el.hasAttribute('capture'),
  });
  const out = { url: location.href, title: document.title, candidates: [], forms: [], chat: [],
                flags: { password: 0, file: 0, fields: [], captcha: [], oauth: [] } };
  // Page-wide flags, in this frame.
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!vis(el)) continue;
    const f = fieldInfo(el);
    if (f.type === 'password') out.flags.password++;
    if (f.type === 'file') out.flags.file++;
    out.flags.fields.push(f);
  }
  for (const el of document.querySelectorAll("iframe[src*='recaptcha' i], iframe[src*='hcaptcha' i], iframe[src*='turnstile' i], iframe[title*='captcha' i], .g-recaptcha, .h-captcha, .cf-turnstile, [id*='captcha' i]"))
    if (vis(el)) out.flags.captcha.push(clean(el.getAttribute('src') || el.className || el.id).slice(0, 120));
  out.frames = Array.from(document.querySelectorAll('iframe')).filter(vis).map(f => f.src || '').slice(0, 20);
  out.text = (document.body ? document.body.innerText : '').slice(0, maxText);
  out.scroll = { y: scrollY, height: document.documentElement.scrollHeight, view: innerHeight };
  // Chat messages: elements that look like one bubble of a conversation.
  const bubbles = document.querySelectorAll(".msg, .message, [class*='chat__message'], [class*='chat-message'], [class*='bubble'], [data-role='bot'] .msg, [data-role='user'] .msg");
  const seen = new Set();
  for (const el of bubbles) {
    if (!vis(el) || el.querySelector('.msg, .message')) continue;
    const t = clean(el.innerText);
    if (!t || seen.has(el)) continue;
    seen.add(el);
    const m = ancestorMarks(el);
    const who = /(\buser\b|--user|--me|outgoing|\bout\b|--out|--right|self)/.test(m) ? 'visitor' : 'bot';
    out.chat.push({ who, text: t.slice(0, 2000), xpath: xpath(el) });
  }
  // Forms: real <form> elements and loose field groups.
  const forms = Array.from(document.querySelectorAll('form'));
  forms.forEach((form, index) => {
    const fields = Array.from(form.querySelectorAll('input, select, textarea'))
      .filter(el => !['hidden', 'submit', 'button', 'image', 'reset'].includes((el.type || '').toLowerCase()))
      .map(fieldInfo);
    const submit = form.querySelector("button[type='submit'], input[type='submit'], button:not([type])");
    out.forms.push({ index, xpath: xpath(form), action: form.action || '', method: (form.method || 'get').toLowerCase(),
      visible: vis(form), in_view: vis(form) && inView(form), fields,
      hidden: Array.from(form.querySelectorAll("input[type='hidden']")).map(h => ({ name: h.name, value: (h.value || '').slice(0, 200) })),
      submit_text: submit ? clean(submit.innerText || submit.value) : '', submit_xpath: submit ? xpath(submit) : '' });
  });
  // Clickables.
  const push = (c) => { if (out.candidates.length < 400) out.candidates.push(c); };
  for (const a of document.querySelectorAll('a[href]')) {
    if (!vis(a)) continue;
    const href = a.href;
    if (!href || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) continue;
    push({ kind: 'link', text: clean(a.innerText || a.getAttribute('aria-label') || a.title).slice(0, 160), href,
           download: a.hasAttribute('download'), target: a.target || '', in_view: inView(a), xpath: xpath(a),
           marks: ancestorMarks(a).slice(0, 300), button_like: /btn|button|cta/.test(marks(a).toLowerCase()) });
  }
  for (const b of document.querySelectorAll("button, input[type='button'], input[type='submit'], [role='button']")) {
    if (!vis(b)) continue;
    if (b.closest('a[href]')) continue;
    const m = ancestorMarks(b);
    const form = b.closest('form');
    const text = clean(b.innerText || b.value || b.getAttribute('aria-label') || b.title).slice(0, 160);
    const isClose = /close|zamknij|dismiss/.test(m + ' ' + (b.getAttribute('aria-label') || '').toLowerCase()) || /^[×✕x]$/i.test(text);
    let kind = 'button';
    if (isClose) kind = 'close';
    else if (/chat|quiz|question|option|answer|survey|ankiet|pytani/.test(m) && !form) kind = 'chat_option';
    push({ kind, text, in_view: inView(b), xpath: xpath(b), marks: m.slice(0, 300),
           form_index: form ? forms.indexOf(form) : null,
           type: (b.getAttribute('type') || '').toLowerCase() });
  }
  for (const el of document.querySelectorAll("input[type='radio'], input[type='checkbox']")) {
    const form = el.closest('form');
    if (form || !vis(el.closest('label') || el)) continue;
    push({ kind: 'chat_option', text: label(el).slice(0, 160), in_view: inView(el), xpath: xpath(el),
           marks: ancestorMarks(el).slice(0, 300), form_index: null, type: el.type });
  }
  for (const el of document.querySelectorAll("textarea, input[type='text'], input:not([type])")) {
    if (!vis(el) || el.closest('form')) continue;
    push({ kind: 'text_input', text: label(el).slice(0, 160), in_view: inView(el), xpath: xpath(el),
           marks: ancestorMarks(el).slice(0, 300), field: fieldInfo(el) });
  }
  for (const v of document.querySelectorAll('video')) {
    if (!vis(v)) continue;
    push({ kind: 'media', text: 'video' + (v.paused ? ' (paused)' : ' (playing)'), in_view: inView(v),
           xpath: xpath(v), paused: v.paused, duration: isFinite(v.duration) ? v.duration : null });
  }
  return out;
}
"""

# Runs in every frame: only what stops a run.
FLAGS_JS = r"""
() => {
  const vis = (el) => el && el.getClientRects && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const fields = [];
  let password = 0, file = 0;
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!vis(el)) continue;
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (type === 'password') password++;
    if (type === 'file') file++;
    let label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    fields.push({ type, name: el.getAttribute('name') || '', id: el.id || '', label,
                  autocomplete: el.getAttribute('autocomplete') || '', required: !!el.required,
                  pattern: el.getAttribute('pattern') || '', inputmode: el.getAttribute('inputmode') || '',
                  visible: true, xpath: '', options: [], accept: el.getAttribute('accept') || '',
                  capture: el.hasAttribute('capture'), tag: el.tagName.toLowerCase(), maxlength: null });
  }
  return { url: location.href, password, file, fields, text: (document.body ? document.body.innerText : '').slice(0, 4000) };
}
"""


@dataclass
class Candidate:
    """One thing the visitor can do. `n` is the number the decider sees."""

    n: int
    kind: str  # link | button | chat_option | close | text_input | media | form | scroll
    text: str
    xpath: str = ""
    href: str = ""
    in_view: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        where = f" -> {self.href}" if self.href else ""
        return f"[{self.n}] {self.kind}: {self.text or '(no text)'}{where}"


@dataclass
class PageState:
    url: str
    title: str
    text: str
    candidates: list[Candidate]
    forms: list[dict[str, Any]]
    chat: list[dict[str, str]]
    flags: dict[str, Any]
    frames: list[str]
    frame_flags: list[dict[str, Any]]
    scroll: dict[str, float]

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()

    def digest(self) -> str:
        """What changes when the page changes: text, chat and the offered actions."""
        blob = "\n".join([self.url, self.text, *(c.describe() for c in self.candidates)])
        return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["text"] = self.text[:2000]
        return data

    def all_fields(self) -> list[dict[str, Any]]:
        """Visible fields in this frame and in every child frame."""
        out = list(self.flags.get("fields", []))
        for frame in self.frame_flags:
            out += frame.get("fields", [])
        return out


def _dedupe_links(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One candidate per link target, keeping the most prominent copy and how often it occurs."""
    by_href: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for c in raw:
        if c["kind"] != "link":
            out.append(c)
            continue
        key = c["href"].split("#")[0]
        prev = by_href.get(key)
        if prev is None:
            c = {**c, "repeats": 1}
            by_href[key] = c
            out.append(c)
            continue
        prev["repeats"] += 1
        if not prev["text"] and c["text"]:
            prev["text"] = c["text"]
        if c["in_view"] and not prev["in_view"]:
            prev.update(in_view=True, xpath=c["xpath"])
    return out


def build_state(raw: dict[str, Any], frame_flags: list[dict[str, Any]]) -> PageState:
    """Turn the script's output into a numbered candidate list (pure, so it can be tested)."""
    items = _dedupe_links(raw.get("candidates", []))
    # In view first, then the rest, keeping page order within each group.
    items.sort(key=lambda c: not c.get("in_view"))
    candidates: list[Candidate] = []
    for c in items[: MAX_CANDIDATES - 4]:
        detail = {
            k: v for k, v in c.items() if k not in {"kind", "text", "xpath", "href", "in_view"}
        }
        candidates.append(
            Candidate(
                n=len(candidates) + 1,
                kind=c["kind"],
                text=c.get("text", ""),
                xpath=c.get("xpath", ""),
                href=c.get("href", ""),
                in_view=bool(c.get("in_view")),
                detail=detail,
            )
        )
    for form in raw.get("forms", []):
        visible = [f for f in form["fields"] if f.get("visible")]
        if not form.get("visible") or not visible:
            continue
        names = ", ".join((f.get("label") or f.get("name") or f.get("type")) for f in visible)
        candidates.append(
            Candidate(
                n=len(candidates) + 1,
                kind="form",
                text=f"fill in and send the form ({names})"
                + (f", button '{form['submit_text']}'" if form.get("submit_text") else ""),
                xpath=form["xpath"],
                href=form.get("action", ""),
                in_view=bool(form.get("in_view")),
                detail={"form_index": form["index"]},
            )
        )
    scroll = raw.get("scroll") or {}
    if scroll and scroll.get("y", 0) + scroll.get("view", 0) < scroll.get("height", 0) - 40:
        candidates.append(Candidate(n=len(candidates) + 1, kind="scroll", text="scroll down"))
    return PageState(
        url=raw.get("url", ""),
        title=raw.get("title", ""),
        text=raw.get("text", ""),
        candidates=candidates,
        forms=raw.get("forms", []),
        chat=raw.get("chat", []),
        flags=raw.get("flags", {}),
        frames=raw.get("frames", []),
        frame_flags=frame_flags,
        scroll=scroll,
    )


def observe(page: Any) -> PageState:
    """Read the page and every child frame. A frame that cannot be read is skipped."""
    raw = page.evaluate(OBSERVE_JS, MAX_TEXT)
    frame_flags = []
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_flags.append(frame.evaluate(FLAGS_JS))
        except Exception:  # noqa: BLE001 - detached or cross-process frame mid-navigation
            frame_flags.append({"url": frame.url, "password": 0, "file": 0, "fields": []})
    return build_state(raw, frame_flags)
