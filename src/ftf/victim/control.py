"""Pause, resume and stop (VV-26), from the side panel, the terminal or the interface page.

The agent thread owns the browser. Other threads (the panel's HTTP server) and other
processes (the Streamlit page, through `control.json`) only set flags; the agent reads them
between steps and while it waits. The current state is written to `live.json` for anyone
watching.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PANEL_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Victim visitor — control</title>
<style>
 body{font:14px/1.4 system-ui,sans-serif;margin:0;padding:14px;background:#111;color:#eee}
 h1{font-size:15px;margin:0 0 8px} .row{margin:6px 0} .k{color:#9aa} .v{font-weight:600}
 #status{display:inline-block;padding:2px 8px;border-radius:9px;background:#264}
 #status.paused,#status.waiting_for_person{background:#a60} #status.stopped,#status.finished{background:#622}
 button{font-size:14px;padding:6px 14px;margin-right:6px;border-radius:6px;border:0;cursor:pointer}
 #log{margin-top:10px;max-height:52vh;overflow:auto;font-size:12px;color:#bcc;white-space:pre-wrap}
 .warn{color:#fc6}
</style></head><body>
<h1>Victim visitor <span id="status">starting</span></h1>
<div class="row"><span class="k">Persona</span> <span class="v" id="persona"></span></div>
<div class="row"><span class="k">Step</span> <span class="v" id="step"></span>
 &nbsp; <span class="k">Stage</span> <span class="v" id="stage"></span></div>
<div class="row"><span class="k">Page</span> <span id="url"></span></div>
<div class="row"><span class="k">Next action</span> <span class="v" id="next"></span></div>
<div class="row"><span class="k">Why</span> <span id="reason"></span></div>
<div class="row warn" id="notice"></div>
<div class="row"><button onclick="send('pause')">Pause</button><button onclick="send('resume')">Resume</button>
<button onclick="send('stop')">Stop</button></div>
<div id="log"></div>
<script>
async function send(c){await fetch('/'+c,{method:'POST'});}
async function tick(){try{const s=await (await fetch('/state')).json();
 for(const k of ['persona','step','stage','url','next','reason','notice'])document.getElementById(k).textContent=s[k]||'';
 const st=document.getElementById('status');st.textContent=s.status;st.className=s.status;
 document.getElementById('log').textContent=(s.log||[]).slice(-40).join('\\n');}catch(e){}}
setInterval(tick,500);tick();
</script></body></html>"""


class RunControl:
    """Flags shared between the agent and whoever controls it."""

    def __init__(self, run_dir: Path | None = None):
        self._lock = threading.Lock()
        self._paused = False
        self._stop = False
        self.run_dir = run_dir
        self.state: dict[str, Any] = {"status": "starting", "log": []}
        self._control_mtime = 0.0

    # -- commands ------------------------------------------------------------------

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def stop(self) -> None:
        with self._lock:
            self._stop, self._paused = True, False

    def command(self, name: str) -> bool:
        action = {"pause": self.pause, "resume": self.resume, "stop": self.stop}.get(name)
        if action is None:
            return False
        action()
        self.note(f"[{name} requested]")
        return True

    def _read_control_file(self) -> None:
        """Commands from another process: `{"command": "pause"}` in run_files/control.json."""
        if self.run_dir is None:
            return
        path = self.run_dir / "control.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        if mtime <= self._control_mtime:
            return
        self._control_mtime = mtime
        try:
            self.command(json.loads(path.read_text(encoding="utf-8")).get("command", ""))
        except (OSError, ValueError, AttributeError):
            pass

    @property
    def paused(self) -> bool:
        self._read_control_file()
        with self._lock:
            return self._paused

    @property
    def stopped(self) -> bool:
        self._read_control_file()
        with self._lock:
            return self._stop

    # -- state ---------------------------------------------------------------------

    def update(self, **fields: Any) -> None:
        with self._lock:
            self.state.update(fields)
            snapshot = dict(self.state)
        self._write(snapshot)

    def note(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            self.state["log"] = [*self.state.get("log", []), f"{stamp} {line}"][-200:]
            snapshot = dict(self.state)
        self._write(snapshot)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def _write(self, snapshot: dict[str, Any]) -> None:
        if self.run_dir is None:
            return
        path = self.run_dir / "live.json"
        temp = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass


def write_command(run_dir: Path, command: str) -> None:
    """For the interface page: ask a running agent in another process to pause, resume or stop."""
    path = Path(run_dir) / "control.json"
    path.write_text(json.dumps({"command": command, "at": time.time()}), encoding="utf-8")


class PanelServer:
    """The small control page shown in its own window next to the browser (VV-26)."""

    def __init__(self, control: RunControl):
        ctl = control

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(body)))
                self.send_header("cache-control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/state"):
                    body = json.dumps(ctl.snapshot(), ensure_ascii=False).encode()
                    self._send(200, body, "application/json")
                else:
                    self._send(200, PANEL_HTML.encode(), "text/html; charset=utf-8")

            def do_POST(self) -> None:
                ok = ctl.command(self.path.strip("/"))
                self._send(200 if ok else 404, b"{}", "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
