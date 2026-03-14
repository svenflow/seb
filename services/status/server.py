#!/usr/bin/env python3
"""seb status page — served at /seb on the server IP."""

import subprocess
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

PORT = 8765


def run(cmd: list[str], timeout: int = 5, env=None) -> str:
  try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return (r.stdout + r.stderr).strip()
  except Exception as e:
    return f"error: {e}"


def get_status() -> dict:
  # Check seb via pgrep (works from any context) and user dbus
  env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/0/bus", "HOME": "/root"}
  seb_active = run(
    ["systemctl", "--user", "is-active", "seb"],
    timeout=5, env={**__import__("os").environ, **env}
  )
  if seb_active not in ("active", "inactive", "failed", "activating"):
    # fallback: check if process is running
    result = run(["pgrep", "-f", "seb.main"])
    seb_active = "active" if result.strip() else "inactive"

  signal_status = run(["docker", "inspect", "--format",
    "{{.State.Status}} ({{.State.Health.Status}})", "seb-signal-cli-1"])

  # Read journal by unit match — works from root without --user --machine
  logs = run(["journalctl", "_SYSTEMD_USER_UNIT=seb.service",
    "-n", "40", "--no-pager", "-o", "short-iso"])

  return {
    "seb": seb_active,
    "signal_cli": signal_status,
    "logs": logs,
    "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
  }


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>seb status</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d0d0d;
      color: #e0e0e0;
      font-family: 'SF Mono', 'Fira Code', monospace;
      padding: 2rem;
      max-width: 960px;
      margin: 0 auto;
    }}
    h1 {{ font-size: 1.4rem; color: #fff; margin-bottom: 0.25rem; }}
    .updated {{ font-size: 0.75rem; color: #666; margin-bottom: 2rem; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem; }}
    .card {{
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 8px;
      padding: 1rem 1.25rem;
    }}
    .card h2 {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: #666; margin-bottom: 0.5rem; }}
    .badge {{
      display: inline-block;
      font-size: 0.85rem;
      font-weight: 600;
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
    }}
    .badge.active {{ background: #0f3; color: #000; }}
    .badge.inactive, .badge.failed {{ background: #f33; color: #fff; }}
    .badge.unknown {{ background: #555; color: #fff; }}
    .logs-card {{
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 8px;
      padding: 1rem 1.25rem;
    }}
    .logs-card h2 {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: #666; margin-bottom: 0.75rem; }}
    pre {{
      font-size: 0.72rem;
      line-height: 1.6;
      white-space: pre-wrap;
      word-break: break-all;
      color: #ccc;
      max-height: 500px;
      overflow-y: auto;
    }}
    .err {{ color: #f77; }}
    .warn {{ color: #fa0; }}
    .info {{ color: #7af; }}
    .note {{ font-size: 0.72rem; color: #555; margin-top: 1rem; text-align: right; }}
  </style>
</head>
<body>
  <h1>seb</h1>
  <p class="updated">updated {updated} &mdash; refreshes every 15s</p>

  <div class="grid">
    <div class="card">
      <h2>seb daemon</h2>
      <span class="badge {seb_cls}">{seb}</span>
    </div>
    <div class="card">
      <h2>signal-cli</h2>
      <span class="badge {signal_cls}">{signal_cli}</span>
    </div>
  </div>

  <div class="logs-card">
    <h2>recent logs (last 40 lines)</h2>
    <pre>{logs_html}</pre>
  </div>

  <p class="note">seb &mdash; sammcgrail/seb</p>
  <script>
    // Scroll log to bottom on load
    window.addEventListener('load', function() {{
      var pre = document.querySelector('pre');
      if (pre) pre.scrollTop = pre.scrollHeight;
    }});
  </script>
</body>
</html>"""


def colorize_logs(logs: str) -> str:
  lines = []
  for line in logs.splitlines():
    if "ERROR" in line or "error" in line.lower() or "Traceback" in line:
      lines.append(f'<span class="err">{_esc(line)}</span>')
    elif "WARNING" in line or "WARN" in line:
      lines.append(f'<span class="warn">{_esc(line)}</span>')
    elif "INFO" in line:
      lines.append(f'<span class="info">{_esc(line)}</span>')
    else:
      lines.append(_esc(line))
  return "\n".join(lines)


def _esc(s: str) -> str:
  return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def badge_cls(status: str) -> str:
  if ("active" in status and "inactive" not in status) or "running" in status:
    return "active"
  if "failed" in status or "inactive" in status or "exited" in status:
    return "failed"
  return "unknown"


class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
    if self.path not in ("/seb", "/seb/", "/"):
      self.send_response(302)
      self.send_header("Location", "/seb")
      self.end_headers()
      return

    s = get_status()
    body = HTML.format(
      updated=s["updated"],
      seb=_esc(s["seb"]),
      seb_cls=badge_cls(s["seb"]),
      signal_cli=_esc(s["signal_cli"]),
      signal_cls=badge_cls(s["signal_cli"]),
      logs_html=colorize_logs(s["logs"]),
    ).encode()

    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def log_message(self, fmt, *args):
    pass  # suppress access logs


if __name__ == "__main__":
  server = HTTPServer(("0.0.0.0", PORT), Handler)
  print(f"seb status server on http://127.0.0.1:{PORT}")
  server.serve_forever()
