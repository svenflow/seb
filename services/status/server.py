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


def parse_logs(raw: str) -> list[dict]:
  """Parse journal lines into structured log entries."""
  entries = []
  for line in raw.splitlines():
    m = _PYLOG_RE.match(line)
    if m:
      time, level, logger, message = m.group(1), m.group(2), m.group(3), m.group(4)
      # Shorten logger name: seb.listeners.signal → listeners.signal
      short_logger = logger.removeprefix("seb.")
      entries.append({"time": time, "level": level, "logger": short_logger, "msg": message})
    else:
      # Traceback lines etc — strip journal prefix if present, show as continuation
      stripped = _JOURNAL_PREFIX_RE.sub("", line).strip()
      if stripped:
        entries.append({"time": "", "level": "RAW", "logger": "", "msg": stripped})
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
  <title>seb status</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: #0d0d0d;
      color: #e0e0e0;
      font-family: 'SF Mono', 'Fira Code', monospace;
      padding: 2rem;
      max-width: 1100px;
      margin: 0 auto;
    }
    h1 { font-size: 1.4rem; color: #fff; margin-bottom: 0.25rem; }
    .meta { font-size: 0.72rem; color: #555; margin-bottom: 2rem; }
    .meta span { color: #888; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem; }
    .card {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 8px;
      padding: 1rem 1.25rem;
    }
    .card h2 { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.1em; color: #555; margin-bottom: 0.5rem; }
    .badge {
      display: inline-block;
      font-size: 0.82rem;
      font-weight: 600;
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
    }
    .badge.active { background: #0c3; color: #000; }
    .badge.inactive, .badge.failed { background: #c33; color: #fff; }
    .badge.unknown { background: #444; color: #ccc; }
    .logs-card {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 8px;
      overflow: hidden;
    }
    .logs-header {
      padding: 0.75rem 1.25rem;
      border-bottom: 1px solid #2a2a2a;
      font-size: 0.65rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: #555;
    }
    .log-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.72rem;
      line-height: 1.5;
    }
    .log-scroll {
      max-height: 540px;
      overflow-y: auto;
    }
    .log-table td { padding: 0.1rem 0.5rem; vertical-align: top; white-space: pre-wrap; word-break: break-all; }
    .log-table .td-time { color: #555; white-space: nowrap; width: 6.5rem; padding-left: 1rem; }
    .log-table .td-level { white-space: nowrap; width: 5rem; font-weight: 600; }
    .log-table .td-logger { color: #666; white-space: nowrap; width: 16rem; }
    .log-table .td-msg { color: #ccc; }
    .log-table tr.raw td { color: #555; font-size: 0.67rem; padding-left: 3rem; }
    .log-table tr.raw .td-msg { color: #666; }
    .level-INFO .td-level { color: #5af; }
    .level-ERROR .td-level, .level-CRITICAL .td-level { color: #f66; }
    .level-ERROR .td-msg, .level-CRITICAL .td-msg { color: #f99; }
    .level-WARNING .td-level { color: #fa0; }
    .level-DEBUG .td-level { color: #555; }
    .note { font-size: 0.7rem; color: #444; margin-top: 1rem; text-align: right; }
  </style>
</head>
<body>
  <h1>seb</h1>
  <p class="meta">updated <span id="updated">—</span> &nbsp;·&nbsp; polling every 10s</p>

  <div class="grid">
    <div class="card">
      <h2>seb daemon</h2>
      <span class="badge" id="seb-badge">—</span>
    </div>
    <div class="card">
      <h2>signal-cli</h2>
      <span class="badge" id="signal-badge">—</span>
    </div>
  </div>

  <div class="logs-card">
    <div class="logs-header">recent logs</div>
    <div class="log-scroll" id="log-scroll">
      <table class="log-table"><tbody id="log-body"></tbody></table>
    </div>
  </div>

  <p class="note">sammcgrail/seb</p>

  <script>
    function badgeCls(s) {
      if (s.includes('active') && !s.includes('inactive') || s.includes('running')) return 'active';
      if (s.includes('failed') || s.includes('inactive') || s.includes('exited')) return 'failed';
      return 'unknown';
    }

    function renderLogs(entries) {
      const tbody = document.getElementById('log-body');
      const scroll = document.getElementById('log-scroll');
      const atBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 40;

      tbody.innerHTML = entries.map(e => {
        if (e.level === 'RAW') {
          return `<tr class="raw"><td class="td-time"></td><td class="td-level"></td><td class="td-logger"></td><td class="td-msg">${esc(e.msg)}</td></tr>`;
        }
        return `<tr class="level-${e.level}">
          <td class="td-time">${esc(e.time)}</td>
          <td class="td-level">${e.level}</td>
          <td class="td-logger">${esc(e.logger)}</td>
          <td class="td-msg">${esc(e.msg)}</td>
        </tr>`;
      }).join('');

      if (atBottom) scroll.scrollTop = scroll.scrollHeight;
    }

    function esc(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function setBadge(id, text) {
      const el = document.getElementById(id);
      el.textContent = text;
      el.className = 'badge ' + badgeCls(text);
    }

    function poll() {
      fetch('/seb/data')
        .then(r => r.json())
        .then(d => {
          document.getElementById('updated').textContent = d.updated;
          setBadge('seb-badge', d.seb);
          setBadge('signal-badge', d.signal_cli);
          renderLogs(d.logs);
        })
        .catch(() => {});
    }

    poll();
    setInterval(poll, 10000);

    // Initial scroll to bottom
    setTimeout(() => {
      const s = document.getElementById('log-scroll');
      s.scrollTop = s.scrollHeight;
    }, 200);
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
    if self.path in ("/seb/data",):
      s = get_status()
      body = json.dumps(s).encode()
      self.send_response(200)
      self.send_header("Content-Type", "application/json")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    elif self.path in ("/seb", "/seb/", "/"):
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
