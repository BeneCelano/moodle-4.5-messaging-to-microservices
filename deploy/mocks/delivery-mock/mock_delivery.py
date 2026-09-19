#!/usr/bin/env python3
"""The SMTP and Airnotifier mock of the laboratory, one process on two ports, with a page to watch them.

    python3 mock_delivery.py [--smtp-port 1025] [--http-port 8081] [--bind 0.0.0.0]

Routes: GET / (page), GET /events (JSON), GET /health, POST /api/v2/push (Airnotifier), POST /fail
and POST /repair (simulated fault: 451 on SMTP, 503 on push), POST /reset (clear the stores).
"""

from __future__ import annotations

import argparse
import email
import email.policy
import json
import socketserver
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Items kept per kind.
CAPACITY = 200

_lock = threading.Lock()
_messages: list[dict] = []
_notifications: list[dict] = []

# Whether the two services refuse what they receive (temporary refusal).
_failing = threading.Event()


def _record(store: list[dict], item: dict) -> None:
    """Add an item at the head, keeping the capacity."""
    with _lock:
        store.insert(0, item)
        del store[CAPACITY:]


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------------------------------


class SmtpHandler(socketserver.StreamRequestHandler):
    """Accept an SMTP delivery and keep it."""

    def handle(self) -> None:
        self._write("220 delivery.lab ESMTP mock")

        sender = ""
        recipients: list[str] = []

        while True:
            line = self.rfile.readline()
            if not line:
                return

            command = line.decode("utf-8", "replace").strip()
            upper = command.upper()

            if upper.startswith("EHLO"):
                # Multi-line answer: all with a dash but the last. No extension beyond
                # SIZE.
                self._write("250-delivery.lab")
                self._write("250-8BITMIME")
                self._write("250 SIZE 10485760")
            elif upper.startswith("HELO"):
                self._write("250 delivery.lab")
            elif upper.startswith("MAIL FROM"):
                sender = _between_angle_brackets(command)
                self._write("250 2.1.0 Sender accepted")
            elif upper.startswith("RCPT TO"):
                recipients.append(_between_angle_brackets(command))
                self._write("250 2.1.5 Recipient accepted")
            elif upper == "DATA":
                if _failing.is_set():
                    # Before reading the body: the client must not even transmit it.
                    self._write("451 4.3.0 Simulated fault, try again later")
                    sender, recipients = "", []
                    continue
                self._write("354 End with <CR><LF>.<CR><LF>")
                raw = self._read_body()
                _record(_messages, _parse_message(raw, sender, recipients))
                sender, recipients = "", []
                self._write("250 2.0.0 Accepted")
            elif upper == "RSET":
                sender, recipients = "", []
                self._write("250 2.0.0 Reset")
            elif upper == "NOOP":
                self._write("250 2.0.0 OK")
            elif upper == "QUIT":
                self._write("221 2.0.0 Bye")
                return
            else:
                self._write("502 5.5.2 Command not recognised")

    def _write(self, line: str) -> None:
        """Send an answer line with the terminator the protocol requires."""
        self.wfile.write(line.encode("utf-8") + b"\r\n")
        self.wfile.flush()

    def _read_body(self) -> bytes:
        """Read up to the line holding the lone dot, undoing the dot stuffing."""
        lines: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line or line in (b".\r\n", b".\n"):
                break
            if line.startswith(b".."):
                line = line[1:]
            lines.append(line)
        return b"".join(lines)


def _between_angle_brackets(command: str) -> str:
    """Extract the address from 'MAIL FROM:<someone@example>' and the like."""
    opening = command.find("<")
    closing = command.rfind(">")
    if opening != -1 and closing > opening:
        return command[opening + 1 : closing]
    # Some clients omit the brackets: take what follows the colon.
    _, _, rest = command.partition(":")
    return rest.strip()


def _parse_message(raw: bytes, sender: str, recipients: list[str]) -> dict:
    """Reduce the received message to the form the page shows."""
    message = email.message_from_bytes(raw, policy=email.policy.default)

    text = ""
    html = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content = part.get_content()
            if part.get_content_subtype() == "html":
                html = content
            elif part.get_content_subtype() == "plain":
                text = content
    else:
        content = message.get_content()
        if message.get_content_subtype() == "html":
            html = content
        else:
            text = content

    return {
        "time": _now(),
        "from": message.get("From", sender),
        "to": message.get("To", ", ".join(recipients)),
        "subject": message.get("Subject", "(no subject)"),
        "text": text.strip(),
        "html": html.strip(),
        "format": "HTML" if html else "text",
    }


class SmtpServer(socketserver.ThreadingTCPServer):
    """SMTP server with address reuse, to restart without waiting."""

    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------------------------------
# HTTP: the Airnotifier interface and the page
# ---------------------------------------------------------------------------------------------------


class HttpHandler(BaseHTTPRequestHandler):
    """Answer the Airnotifier interface and serve the page."""

    # Silence the per-request log.
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - name imposed by the library
        """Accept a push notification, switch the fault, or clear the store."""
        route = self.path.rstrip("/")
        if route == "/reset":
            with _lock:
                _messages.clear()
                _notifications.clear()
            self._json(200, {"outcome": "cleared"})
            return

        if route == "/fail":
            _failing.set()
            self._json(200, {"failing": True})
            return

        if route == "/repair":
            _failing.clear()
            self._json(200, {"failing": False})
            return

        if route != "/api/v2/push":
            self._json(404, {"error": "unknown path"})
            return

        if _failing.is_set():
            # 503: a server code, retryable.
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._json(503, {"error": "simulated fault"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            self._json(400, {"error": f"body not parseable: {error}"})
            return

        # The two headers with which Airnotifier identifies the application.
        _record(
            _notifications,
            {
                "time": _now(),
                "device": body.get("deviceid", "(missing)"),
                "platform": body.get("device", ""),
                "title": body.get("title", ""),
                "alert": body.get("alert", ""),
                "extra": body.get("extra") or None,
                "app": self.headers.get("X-AN-APP-NAME", ""),
                "key_present": bool(self.headers.get("X-AN-APP-KEY")),
            },
        )
        self._json(200, {"status": 200, "message": "accepted"})

    def do_GET(self) -> None:  # noqa: N802 - name imposed by the library
        """Serve the page, the data it polls, or the health probe."""
        route = self.path.split("?")[0].rstrip("/") or "/"

        if route == "/events":
            with _lock:
                self._json(
                    200,
                    {
                        "messages": list(_messages),
                        "notifications": list(_notifications),
                        "failing": _failing.is_set(),
                    },
                )
        elif route == "/health":
            self._json(200, {"status": "alive", "failing": _failing.is_set()})
        elif route == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"error": "unknown path"})

    def _json(self, code: int, payload: dict) -> None:
        """Answer with a JSON object."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Delivery &mdash; laboratory simulator</title>
<style>
  /* Light theme, to sit next to the demo dashboard, which is light too. */
  :root {
    --bg: #f4f5f7; --panel: #ffffff; --border: #d8dade;
    --text: #1f2937; --muted: #6b7280; --accent: #3d5a80; --green: #2f855a;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size: 20px; margin: 0 0 4px; font-weight: 600; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .subtitle b { color: var(--accent); font-weight: 600; }
  .columns { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 18px; }
  .column { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
            padding: 14px; min-height: 160px; }
  .column h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
               color: var(--muted); margin: 0 0 12px; font-weight: 600; }
  .count { color: var(--accent); }
  .card { background: #f8f9fb; border: 1px solid var(--border); border-radius: 10px;
          padding: 10px 12px; margin-bottom: 10px; animation: enter .35s ease; }
  @keyframes enter { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; } }
  .row { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
  .title { font-weight: 600; }
  .time { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .meta { color: var(--muted); font-size: 12px; margin-top: 2px; word-break: break-all; }
  .body { margin-top: 6px; white-space: pre-wrap; }
  .tag { display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 5px;
         background: #e6ecf5; color: var(--accent); margin-left: 6px; }
  .empty { color: var(--muted); font-style: italic; font-size: 13px; }
  .footer { margin-top: 22px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--border);
            padding-top: 12px; }
  .button { background: transparent; color: var(--muted); border: 1px solid var(--border);
            border-radius: 7px; padding: 4px 10px; font: inherit; font-size: 12px; cursor: pointer; }
  .button:hover { color: var(--text); border-color: var(--accent); }
  .fault { display: none; background: #fdecee; border: 1px solid #f0b4bb; color: #8a1c26;
           border-radius: 10px; padding: 10px 14px; margin-bottom: 18px; font-size: 14px; }
  .fault.on { display: block; }
</style>
</head>
<body>
  <h1>Delivery &mdash; laboratory simulator</h1>
  <div class="subtitle">
    Receives what the DeliveryGateway sends out: SMTP for mail, the Airnotifier interface for push
    notifications. The last leg &mdash; from the push service to the physical device &mdash;
    <b>is not reproduced</b>.
    <button class="button" onclick="reset()">clear</button>
  </div>
  <div class="fault" id="fault">
    <b>Simulated fault active.</b> Both services refuse every delivery with a temporary error
    &mdash; 503 on the Airnotifier interface, 451 on SMTP &mdash; so the gateway has to retry.
    Restore with <code>POST /repair</code>.
  </div>
  <div class="columns" id="columns"></div>
  <div class="footer" id="footer">waiting&hellip;</div>

<script>
function card(inner) { return '<div class="card">' + inner + '</div>'; }
function plural(n, name) { return n + ' ' + name + (n === 1 ? '' : 's'); }

// The page polls the simulator every second and redraws only when the answer changed: rebuilding
// the nodes replays the entrance animation, a continuous flicker on a still screen.
var lastState = '';
function esc(t) {
  return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

function draw(data) {
  var signature = JSON.stringify(data);
  if (signature === lastState) { return; }
  lastState = signature;

  var columns = [];

  var mail = data.messages.map(function (m) {
    return card(
      '<div class="row"><span class="title">' + esc(m.subject) + '</span>' +
      '<span class="time">' + esc(m.time) + '</span></div>' +
      '<div class="meta">to ' + esc(m.to) + ' &middot; from ' + esc(m.from) +
      '<span class="tag">' + esc(m.format) + '</span></div>' +
      '<div class="body">' + esc(m.text || m.html) + '</div>'
    );
  }).join('');
  columns.push(
    '<div class="column"><h2>Inbox <span class="count">' +
    data.messages.length + '</span></h2>' +
    (mail || '<div class="empty">no mail received</div>') + '</div>'
  );

  // One column per push token: the unit the gateway really delivers to, since airnotifier delivers to
  // the user's devices, not to the user.
  var byDevice = {};
  data.notifications.forEach(function (n) {
    (byDevice[n.device] = byDevice[n.device] || []).push(n);
  });

  Object.keys(byDevice).sort().forEach(function (token) {
    var cards = byDevice[token].map(function (n) {
      return card(
        '<div class="row"><span class="title">' + esc(n.title || '(no title)') + '</span>' +
        '<span class="time">' + esc(n.time) + '</span></div>' +
        '<div class="body">' + esc(n.alert) + '</div>' +
        '<div class="meta">' + esc(n.app) +
        (n.key_present ? ' &middot; key present' : ' &middot; <b>key missing</b>') +
        (n.extra ? ' &middot; with presentation block' : ' &middot; plain') +
        '</div>'
      );
    }).join('');
    columns.push(
      '<div class="column"><h2>Device <span class="count">' +
      esc(token) + '</span></h2>' + cards + '</div>'
    );
  });

  if (!data.notifications.length) {
    columns.push(
      '<div class="column"><h2>Devices</h2>' +
      '<div class="empty">no push notification received</div></div>'
    );
  }

  document.getElementById('columns').innerHTML = columns.join('');
  document.getElementById('fault').className = data.failing ? 'fault on' : 'fault';
  document.getElementById('footer').textContent =
    plural(data.messages.length, 'email') + ' and ' +
    plural(data.notifications.length, 'push notification') +
    ' received \\u00b7 last change ' + new Date().toLocaleTimeString();
}

function refresh() {
  fetch('/events').then(function (r) { return r.json(); }).then(draw).catch(function () {
    document.getElementById('footer').textContent = 'the simulator is not responding';
  });
}

function reset() {
  fetch('/reset', { method: 'POST' }).then(refresh);
}

refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------------------------------


def main() -> int:
    """Bring up the two servers and stay listening."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--smtp-port", "--porta-smtp", type=int, default=1025, help="SMTP port. Default 1025.")
    parser.add_argument("--http-port", "--porta-http", type=int, default=8081, help="HTTP port. Default 8081.")
    parser.add_argument(
        "--bind",
        "--indirizzo",
        default="0.0.0.0",
        help="Address to listen on. Default 0.0.0.0.",
    )
    options = parser.parse_args()

    smtp = SmtpServer((options.bind, options.smtp_port), SmtpHandler)
    http = ThreadingHTTPServer((options.bind, options.http_port), HttpHandler)

    threading.Thread(target=smtp.serve_forever, daemon=True).start()
    threading.Thread(target=http.serve_forever, daemon=True).start()

    print(f"SMTP listening on {options.bind}:{options.smtp_port}")
    print(f"Airnotifier listening on {options.bind}:{options.http_port}/api/v2/push/")
    print(f"Page on http://{options.bind}:{options.http_port}/")
    print("Stop with Ctrl-C.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nShutting down.")
        smtp.shutdown()
        http.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
