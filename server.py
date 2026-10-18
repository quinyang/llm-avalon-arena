"""The live table: serves the viewer and lets people with the passcode start a new game.

    export ASI_ONE_API_KEY='...'
    export AVALON_PASSCODE='pick-something'      # share with judges only; without it, starting is off
    python server.py                              # http://localhost:8000
    cloudflared tunnel --url http://localhost:8000   # public https link, free, no account

Safety: one game at a time, a daily cap (AVALON_MAX_GAMES, default 6), a short cooldown, a passcode
checked in constant time, and only the viewer, transcripts and results chart are served (never the
code folder, .git or .venv). The API key stays in this process and never reaches the browser.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import posixpath
import random
import threading
import time
import traceback
import urllib.parse
from datetime import date, datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from roles import ROLES_5P
from run import PROVIDER_DEFAULT_MODELS, _atomic_write, default_provider, make_client, play_game

ROOT = Path(__file__).parent.resolve()
TRANSCRIPTS = ROOT / "transcripts"


class Table:
    """Runs at most one game at a time in a background thread."""

    def __init__(self, provider: str, model: str | None, passcode: str | None, max_games: int,
                 cooldown_s: int, fake_delay: float = 0.0):
        self.provider = provider
        self.model = model or PROVIDER_DEFAULT_MODELS[provider]
        self.passcode = passcode
        self.max_games = max_games
        self.cooldown_s = cooldown_s
        self.fake_delay = fake_delay
        self.lock = threading.Lock()
        self.running: dict | None = None      # {"live_file", "started_at", "seed"}
        self.last_finished = 0.0
        self.day, self.count = date.today(), 0
        self.last_error: str | None = None
        self.client = None

    def status(self) -> dict:
        with self.lock:
            if date.today() != self.day:
                self.day, self.count = date.today(), 0
            cooldown = max(0, int(self.cooldown_s - (time.time() - self.last_finished))) if self.last_finished else 0
            return {
                "server": True,
                "can_start": bool(self.passcode),
                "running": self.running,
                "games_left_today": max(0, self.max_games - self.count),
                "cooldown_s": cooldown,
                "model": self.model,
                "last_error": self.last_error,
            }

    def start(self, passcode: str) -> tuple[int, dict]:
        if not self.passcode:
            return 403, {"error": "Starting games is turned off on this server."}
        if not hmac.compare_digest(passcode.encode(), self.passcode.encode()):
            time.sleep(1.0)  # slow down guessing
            return 403, {"error": "That passcode is not right."}
        st = self.status()
        with self.lock:
            if self.running:
                return 409, {"error": "A game is already being played. Watch it live instead.", "running": self.running}
            if st["games_left_today"] <= 0:
                return 429, {"error": "Today's game limit is used up."}
            if st["cooldown_s"] > 0:
                return 429, {"error": f"The table needs {st['cooldown_s']} more seconds before the next game."}
            seed = random.randrange(10**6)
            live = f"live_{seed}.json"
            self.running = {"live_file": live, "started_at": datetime.now().isoformat(timespec="seconds"), "seed": seed}
            self.count += 1
            self.last_error = None
        threading.Thread(target=self._play, args=(seed, TRANSCRIPTS / live), daemon=True).start()
        return 202, {"ok": True, "live_file": live}

    def _client(self):
        if self.client is None:
            self.client = make_client(self.provider, random.randrange(10**6))
            if self.fake_delay:  # testing only: make the fake model as slow as a real one
                create = self.client.messages.create
                def slow(**kw):
                    time.sleep(self.fake_delay)
                    return create(**kw)
                self.client.messages.create = slow
        return self.client

    def _play(self, seed: int, live: Path) -> None:
        cfg = {"models": {r.value: self.model for r in ROLES_5P}, "discussion_rounds": 1, "word_limit": 40,
               "fake": self.provider == "fake", "provider": self.provider}
        try:
            tr = play_game(cfg, self._client(), seed, verbose=False, live_path=live)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            prefix = "fake_" if cfg["fake"] else ""
            final = TRANSCRIPTS / f"{prefix}{stamp}_{tr['game_id'][:8]}.json"
            _atomic_write(final, tr)
            print(f"[table] finished: {tr['result']['winner']} via {tr['result']['reason']} -> {final.name}")
        except Exception as e:  # keep the server alive; report it in /api/status
            self.last_error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            live.unlink(missing_ok=True)
            with self.lock:
                self.running = None
                self.last_finished = time.time()


TABLE: Table | None = None


class Handler(SimpleHTTPRequestHandler):
    """Serves only: / (viewer), /viewer.html, /transcripts/ (listing + .json), /analysis/summary.png, /api/*."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):  # quieter console: only API calls
        if "/api/" in str(args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self, raw: str) -> bool:
        path = urllib.parse.unquote(raw.split("?", 1)[0].split("#", 1)[0])
        if path in ("/", "/viewer.html", "/transcripts/", "/analysis/summary.png"):
            return True
        norm = posixpath.normpath(path)
        # exactly one plain .json file directly inside transcripts/, no traversal or subfolders
        if norm != path or not norm.startswith("/transcripts/") or not norm.endswith(".json"):
            return False
        name = norm[len("/transcripts/"):]
        if "/" in name or "\\" in name or name.startswith("."):
            return False
        return not name.startswith("fake_") or (TABLE is not None and TABLE.provider == "fake")

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._json(200, TABLE.status())
        if not self._allowed(self.path):
            return self.send_error(HTTPStatus.NOT_FOUND)
        if self.path.split("?", 1)[0] == "/":
            self.path = "/viewer.html"
        return super().do_GET()

    def do_HEAD(self):
        if not self._allowed(self.path):
            return self.send_error(HTTPStatus.NOT_FOUND)
        return super().do_HEAD()

    def do_POST(self):
        if self.path != "/api/start":
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 4096)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "Bad request."})
        code, out = TABLE.start(str(body.get("passcode", "")))
        return self._json(code, out)

    def end_headers(self):
        if self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")  # live files change every few seconds
        super().end_headers()


def main() -> None:
    global TABLE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1 when using a tunnel")
    ap.add_argument("--provider", choices=["anthropic", "asi", "fake"], default=default_provider())
    ap.add_argument("--model", default=os.environ.get("AVALON_MODEL"))
    ap.add_argument("--max-games", type=int, default=int(os.environ.get("AVALON_MAX_GAMES", 6)))
    ap.add_argument("--cooldown", type=int, default=60, help="seconds between games")
    ap.add_argument("--fake-delay", type=float, default=0.0, help=argparse.SUPPRESS)
    args = ap.parse_args()

    TRANSCRIPTS.mkdir(exist_ok=True)
    for stale in TRANSCRIPTS.glob("live_*.json"):  # leftovers from a crash would show as fake live games
        stale.unlink()
    passcode = os.environ.get("AVALON_PASSCODE") or None
    TABLE = Table(args.provider, args.model, passcode, args.max_games, args.cooldown, args.fake_delay)
    print(f"Avalon table on http://{args.host}:{args.port}  provider={args.provider} model={TABLE.model}")
    print("Starting games: " + (f"ON (passcode set, {args.max_games}/day)" if passcode else
                                "OFF (set AVALON_PASSCODE to let judges start games)"))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
