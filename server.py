#!/usr/bin/env python3
"""Use your phone as an air mouse for this Mac.

Serves a web page over HTTPS on the local network. The page reads the phone's
gyroscope and touch input and streams it back over a WebSocket; this process
turns it into real cursor movement, clicks and scrolling with Quartz events.
"""

import argparse
import json
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import Quartz
from aiohttp import WSMsgType, web
from ApplicationServices import AXIsProcessTrusted

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
STATE_DIR = ROOT / ".state"

DOUBLE_CLICK_SECONDS = 0.4
DOUBLE_CLICK_SLOP_PX = 6


class Mouse:
    """Posts synthetic mouse events.

    Deltas arrive as floats; the fractional part is carried over so slow,
    precise movement isn't lost to rounding.
    """

    # name -> (button, down event, up event, dragged event)
    BUTTONS = {
        "left": (
            Quartz.kCGMouseButtonLeft,
            Quartz.kCGEventLeftMouseDown,
            Quartz.kCGEventLeftMouseUp,
            Quartz.kCGEventLeftMouseDragged,
        ),
        "right": (
            Quartz.kCGMouseButtonRight,
            Quartz.kCGEventRightMouseDown,
            Quartz.kCGEventRightMouseUp,
            Quartz.kCGEventRightMouseDragged,
        ),
    }

    def __init__(self):
        self.held = set()
        self.move_rem = [0.0, 0.0]
        self.scroll_rem = [0.0, 0.0]
        self.displays = []
        self.displays_at = 0.0
        self.last_click_button = None
        self.last_click_time = 0.0
        self.last_click_pos = (0.0, 0.0)
        self.click_count = 0
        self.trusted = False
        self.trusted_at = 0.0

    def _can_post(self):
        """Posted events are silently dropped without Accessibility permission."""
        now = time.monotonic()
        if now - self.trusted_at > 2:
            self.trusted = AXIsProcessTrusted()
            self.trusted_at = now
        return self.trusted

    @staticmethod
    def _position():
        return Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))

    @staticmethod
    def _post(event):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _clamp(self, x, y):
        """Keep the point on the nearest display (works for any monitor layout)."""
        now = time.monotonic()
        if now - self.displays_at > 2:
            _, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
            self.displays = [Quartz.CGDisplayBounds(d) for d in ids[:count]]
            self.displays_at = now

        best = None
        for r in self.displays:
            cx = min(max(x, r.origin.x), r.origin.x + r.size.width - 1)
            cy = min(max(y, r.origin.y), r.origin.y + r.size.height - 1)
            dist = (cx - x) ** 2 + (cy - y) ** 2
            if best is None or dist < best[0]:
                best = (dist, cx, cy)
        return (best[1], best[2]) if best else (x, y)

    @staticmethod
    def _take_whole(rem, dx, dy):
        rem[0] += dx
        rem[1] += dy
        ix, iy = int(rem[0]), int(rem[1])
        rem[0] -= ix
        rem[1] -= iy
        return ix, iy

    def move(self, dx, dy):
        ix, iy = self._take_whole(self.move_rem, dx, dy)
        if not ix and not iy:
            return
        pos = self._position()
        x, y = self._clamp(pos.x + ix, pos.y + iy)

        if not self._can_post():
            # Warping needs no permission, so movement still works; clicks won't.
            Quartz.CGWarpMouseCursorPosition((x, y))
            return

        if "left" in self.held:
            button, _, _, event_type = self.BUTTONS["left"]
        elif "right" in self.held:
            button, _, _, event_type = self.BUTTONS["right"]
        else:
            button, event_type = Quartz.kCGMouseButtonLeft, Quartz.kCGEventMouseMoved

        event = Quartz.CGEventCreateMouseEvent(None, event_type, (x, y), button)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaX, ix)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventDeltaY, iy)
        self._post(event)

    def _next_click_count(self, name, pos):
        """macOS only sees a double click if the second press says clickState=2."""
        now = time.monotonic()
        repeat = (
            name == self.last_click_button
            and now - self.last_click_time < DOUBLE_CLICK_SECONDS
            and abs(pos.x - self.last_click_pos[0]) <= DOUBLE_CLICK_SLOP_PX
            and abs(pos.y - self.last_click_pos[1]) <= DOUBLE_CLICK_SLOP_PX
        )
        self.click_count = self.click_count + 1 if repeat else 1
        self.last_click_button = name
        self.last_click_time = now
        self.last_click_pos = (pos.x, pos.y)
        return self.click_count

    def button(self, name, down):
        if name not in self.BUTTONS or (name in self.held) == down:
            return
        button, down_type, up_type, _ = self.BUTTONS[name]
        pos = self._position()
        if down:
            self.held.add(name)
            clicks = self._next_click_count(name, pos)
        else:
            self.held.discard(name)
            clicks = self.click_count if name == self.last_click_button else 1

        event = Quartz.CGEventCreateMouseEvent(None, down_type if down else up_type, pos, button)
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, clicks)
        self._post(event)

    def click(self, name):
        self.button(name, True)
        self.button(name, False)

    def scroll(self, dx, dy):
        ix, iy = self._take_whole(self.scroll_rem, dx, dy)
        if ix or iy:
            self._post(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 2, iy, ix))

    def release_all(self):
        for name in list(self.held):
            self.button(name, False)


def dispatch(mouse, raw):
    """Apply one batch of messages from the phone (a JSON list, or a single object)."""
    try:
        messages = json.loads(raw)
    except json.JSONDecodeError:
        return
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return

    for m in messages:
        try:
            kind = m["t"]
            if kind == "move":
                mouse.move(float(m["dx"]), float(m["dy"]))
            elif kind == "scroll":
                mouse.scroll(float(m["dx"]), float(m["dy"]))
            elif kind == "button":
                mouse.button(m["b"], bool(m["down"]))
            elif kind == "click":
                mouse.click(m["b"])
        except (KeyError, TypeError, ValueError):
            continue


def make_app(mouse, token):
    def authorized(request):
        return secrets.compare_digest(request.query.get("token", ""), token)

    async def index(request):
        return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def air_js(request):
        return web.FileResponse(STATIC_DIR / "air.js", headers={"Cache-Control": "no-store"})

    async def ping(request):
        if not authorized(request):
            raise web.HTTPForbidden()
        return web.Response(status=204)

    async def websocket(request):
        if not authorized(request):
            raise web.HTTPForbidden()
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        print(f"Phone connected from {request.remote}")
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    dispatch(mouse, msg.data)
        finally:
            mouse.release_all()
            print(f"Phone disconnected ({request.remote})")
        return ws

    async def post_input(request):
        # Fallback for browsers that refuse WebSockets on a self-signed certificate.
        if not authorized(request):
            raise web.HTTPForbidden()
        dispatch(mouse, await request.text())
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/air.js", air_js)
    app.router.add_get("/ping", ping)
    app.router.add_get("/ws", websocket)
    app.router.add_post("/input", post_input)
    return app


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))  # no packets are sent; just picks the outbound interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def ensure_certificate(ip):
    """Phones only expose motion sensors to secure pages, so we need HTTPS."""
    cert, key = STATE_DIR / "cert.pem", STATE_DIR / "key.pem"
    if not (cert.exists() and key.exists()):
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(key), "-out", str(cert), "-days", "825",
                "-subj", "/CN=Remote Mouse",
                "-addext", f"subjectAltName=IP:{ip},DNS:localhost",
            ],
            check=True,
            capture_output=True,
        )
    return cert, key


def load_token():
    """Kept across restarts so a bookmarked link on the phone keeps working."""
    path = STATE_DIR / "token"
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(9)
    path.write_text(token)
    path.chmod(0o600)
    return token


def check_accessibility():
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
        print(
            "\n⚠️  Cursor movement works, but clicks and scrolling need permission:\n"
            "   System Settings → Privacy & Security → Accessibility → enable your terminal app,\n"
            "   then restart this script.\n"
        )


def print_qr(url):
    try:
        import qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--reset-token", action="store_true", help="invalidate the old phone link")
    args = parser.parse_args()

    STATE_DIR.mkdir(exist_ok=True)
    if args.reset_token:
        (STATE_DIR / "token").unlink(missing_ok=True)

    ip = lan_ip()
    cert, key = ensure_certificate(ip)
    token = load_token()
    url = f"https://{ip}:{args.port}/?token={token}"

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(cert, key)

    check_accessibility()
    print("Open this on your phone (same Wi-Fi):\n")
    print_qr(url)
    print(f"\n  {url}\n")
    print("Your browser will warn about the self-signed certificate: choose")
    print("'Show Details → visit this website' (Safari) or 'Advanced → Proceed' (Chrome).\n")
    sys.stdout.flush()

    web.run_app(make_app(Mouse(), token), host=args.host, port=args.port, ssl_context=ssl_context, print=None)


if __name__ == "__main__":
    main()
