#!/usr/bin/env python3
"""seb status page — served at /seb on the server IP."""

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

PORT = 8765

# Journal line pattern:
# 2026-03-14T05:44:26+00:00 hostname proc[pid]: 2026-03-14 05:44:26,657 [LEVEL] logger: message
# or just raw output (tracebacks, etc.)
_PYLOG_RE = re.compile(
  r"^\S+\s+\S+\s+\S+:\s+"           # journal prefix (ts host proc[pid]: )
  r"\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d+\s+"  # python ts → group 1 (HH:MM:SS)
  r"\[(INFO|ERROR|WARNING|DEBUG|CRITICAL)\]\s+"        # level → group 2
  r"([\w\.]+):\s+"                   # logger name → group 3
  r"(.+)$"                           # message → group 4
)
_JOURNAL_PREFIX_RE = re.compile(r"^\S+\s+\S+\s+\S+:\s+")


def run(cmd: list[str], timeout: int = 5, env=None) -> str:
  try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return (r.stdout + r.stderr).strip()
  except Exception as e:
    return f"error: {e}"


def get_status() -> dict:
  env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/0/bus", "HOME": "/root"}
  seb_active = run(
    ["systemctl", "--user", "is-active", "seb"],
    timeout=5, env={**os.environ, **env}
  )
  if seb_active not in ("active", "inactive", "failed", "activating"):
    result = run(["pgrep", "-f", "seb.main"])
    seb_active = "active" if result.strip() else "inactive"

  signal_status = run(["docker", "inspect", "--format",
    "{{.State.Status}} ({{.State.Health.Status}})", "seb-signal-cli-1"])

  raw_logs = run(["journalctl", "_SYSTEMD_USER_UNIT=seb.service",
    "-n", "80", "--no-pager", "-o", "short-iso"])

  return {
    "seb": seb_active,
    "signal_cli": signal_status,
    "logs": parse_logs(raw_logs),
    "updated": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
  }


_LOGGER_ABBREV = {
  "__main__":              "main",
  "listeners.signal":      "signal",
  "listeners.telegram":    "telegram",
  "sdk_session":           "session",
  "sdk_backend":           "backend",
  "manager":               "mgr",
  "httpx":                 "httpx",
  "anthropic._base_client":"anthropic",
}


def _abbrev_logger(logger: str) -> str:
  s = logger.removeprefix("seb.")
  return _LOGGER_ABBREV.get(s, s.split(".")[-1])


def parse_logs(raw: str) -> list[dict]:
  entries = []
  for line in raw.splitlines():
    m = _PYLOG_RE.match(line)
    if m:
      time, level, logger, message = m.group(1), m.group(2), m.group(3), m.group(4)
      entries.append({"time": time[:-3], "level": level[:4], "src": _abbrev_logger(logger), "msg": message})
    else:
      stripped = _JOURNAL_PREFIX_RE.sub("", line).strip()
      if stripped:
        entries.append({"time": "", "level": "", "src": "", "msg": stripped})
  return entries


def badge_cls(status: str) -> str:
  if ("active" in status and "inactive" not in status) or "running" in status:
    return "active"
  if "failed" in status or "inactive" in status or "exited" in status:
    return "failed"
  return "unknown"


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>seb</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d0d0d;
      color: #e0e0e0;
      font-family: 'SF Mono', 'Fira Code', monospace;
      font-size: 13px;
      padding: 1.25rem;
      max-width: 900px;
      margin: 0 auto;
    }
    h1 { font-size: 1.1rem; color: #fff; display: inline; }
    .meta { display: inline; font-size: 0.7rem; color: #555; margin-left: 0.75rem; }
    .meta span { color: #777; }
    .top { margin-bottom: 1rem; }
    .badges { display: flex; gap: 0.6rem; margin-top: 0.75rem; flex-wrap: wrap; }
    .badge {
      font-size: 0.72rem; font-weight: 600;
      padding: 0.15rem 0.55rem; border-radius: 3px;
    }
    .badge-label { font-size: 0.65rem; color: #555; margin-right: 0.3rem; }
    .badge.active { background: #0c3; color: #000; }
    .badge.failed, .badge.inactive { background: #c33; color: #fff; }
    .badge.unknown { background: #333; color: #888; }
    .logs-card {
      background: #141414;
      border: 1px solid #242424;
      border-radius: 6px;
      overflow: hidden;
    }
    .log-scroll {
      height: 70vh;
      overflow-y: auto;
    }
    .log-row {
      display: grid;
      grid-template-columns: 5.2rem 3rem 7rem 1fr;
      align-items: baseline;
      padding: 0.08rem 0.75rem;
      border-bottom: 1px solid #1a1a1a;
      line-height: 1.55;
    }
    .log-row:last-child { border-bottom: none; }
    .log-row.raw {
      grid-template-columns: 1fr;
      padding-left: 2rem;
    }
    .l-time { color: #3a3a3a; font-size: 0.68rem; white-space: nowrap; }
    .l-lvl  { font-size: 0.68rem; font-weight: 700; white-space: nowrap; }
    .l-src  { color: #4a4a4a; font-size: 0.68rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 0.5rem; }
    .l-msg  { color: #bbb; font-size: 0.72rem; word-break: break-word; }
    .raw .l-msg { color: #444; font-size: 0.67rem; }
    .INFO  .l-lvl { color: #48a; }
    .WARN  .l-lvl { color: #c80; }
    .ERRO  .l-lvl, .CRIT .l-lvl { color: #c44; }
    .ERRO  .l-msg, .CRIT .l-msg { color: #d88; }
    .DEBU  .l-lvl { color: #444; }
    /* mobile: hide time + src, tighter padding */
    @media (max-width: 540px) {
      body { padding: 0.75rem; }
      .log-row {
        grid-template-columns: 2.8rem 1fr;
        padding: 0.1rem 0.5rem;
      }
      .log-row.raw { padding-left: 1rem; }
      .l-time, .l-src { display: none; }
    }
  </style>
</head>
<body>
  <div class="top">
    <h1>seb</h1>
    <span class="meta">updated <span id="updated">—</span></span>
    <div class="badges">
      <span><span class="badge-label">daemon</span><span class="badge" id="seb-badge">—</span></span>
      <span><span class="badge-label">signal-cli</span><span class="badge" id="signal-badge">—</span></span>
    </div>
  </div>

  <div class="logs-card">
    <div class="log-scroll" id="log-scroll">
      <div id="log-body"></div>
    </div>
  </div>

  <script>
    function badgeCls(s) {
      if ((s.includes('active') && !s.includes('inactive')) || s.includes('running')) return 'active';
      if (s.includes('failed') || s.includes('inactive') || s.includes('exited')) return 'failed';
      return 'unknown';
    }

    function esc(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function renderLogs(entries) {
      const body = document.getElementById('log-body');
      const scroll = document.getElementById('log-scroll');
      const atBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 60;

      body.innerHTML = entries.map(e => {
        if (!e.level) {
          return `<div class="log-row raw"><span class="l-msg">${esc(e.msg)}</span></div>`;
        }
        const lvl = e.level.substring(0,4);
        return `<div class="log-row ${lvl}">
          <span class="l-time">${esc(e.time)}</span>
          <span class="l-lvl">${lvl}</span>
          <span class="l-src">${esc(e.src)}</span>
          <span class="l-msg">${esc(e.msg)}</span>
        </div>`;
      }).join('');

      if (atBottom) scroll.scrollTop = scroll.scrollHeight;
    }

    function poll() {
      fetch('/seb/data')
        .then(r => r.json())
        .then(d => {
          document.getElementById('updated').textContent = d.updated;
          const sb = document.getElementById('seb-badge');
          sb.textContent = d.seb; sb.className = 'badge ' + badgeCls(d.seb);
          const sg = document.getElementById('signal-badge');
          sg.textContent = d.signal_cli; sg.className = 'badge ' + badgeCls(d.signal_cli);
          renderLogs(d.logs);
        })
        .catch(() => {});
    }

    poll();
    setInterval(poll, 10000);
    setTimeout(() => {
      const s = document.getElementById('log-scroll');
      s.scrollTop = s.scrollHeight;
    }, 300);
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
    if self.path in ("/seb/data", "/data"):
      s = get_status()
      body = json.dumps(s).encode()
      self.send_response(200)
      self.send_header("Content-Type", "application/json")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    elif self.path in ("/seb", "/seb/", "/", ""):
      body = HTML.encode()
      self.send_response(200)
      self.send_header("Content-Type", "text/html; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    else:
      self.send_response(302)
      self.send_header("Location", "/seb")
      self.end_headers()

  def log_message(self, fmt, *args):
    pass


if __name__ == "__main__":
  server = HTTPServer(("0.0.0.0", PORT), Handler)
  print(f"seb status server on http://0.0.0.0:{PORT}")
  server.serve_forever()
