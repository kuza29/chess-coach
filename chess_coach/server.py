"""Локальный HTTP-мост между браузером и Stockfish.

POST /api/position   — userscript присылает расстановку + чей ход
GET  /api/state      — панель и userscript забирают оценку и лучший ход
POST /api/settings   — скорость анализа, число линий
GET  /api/health     — проверка связи
GET  /test           — тестовая доска (проверка распознавания без chess.com)
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import chess

from .engine import Analyzer, app_dir, find_engine
from .positions import InvalidPosition, Tracker


def asset(*parts: str) -> Path:
    """Ищет файл и в проекте, и в собранном .exe."""
    bases = [app_dir(), Path(__file__).resolve().parent.parent, Path(__file__).resolve().parent]
    if getattr(sys, "_MEIPASS", None):
        bases.insert(0, Path(sys._MEIPASS))  # type: ignore[attr-defined]
    for base in bases:
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return app_dir().joinpath(*parts)


HOST = "127.0.0.1"
PORT = 8765


class App:
    def __init__(self, engine_path: str | None = None, movetime: float = 0.5,
                 threads: int | None = None, multipv: int = 1) -> None:
        self.tracker = Tracker()
        try:
            engine = find_engine(engine_path)
        except FileNotFoundError as exc:
            engine = ""
            self.engine_error = str(exc)
        else:
            self.engine_error = ""
        self.analyzer = Analyzer(
            engine, movetime=movetime, threads=threads, multipv=multipv
        )
        self.lock = threading.Lock()
        self.pos_id = 0
        self.fen = ""
        self.turn = "w"
        self.source = ""
        self.exact = False
        self.received = 0.0
        self.last_error = ""

    # ------------------------------------------------------------------

    def start(self) -> "App":
        self.analyzer.start()
        return self

    def stop(self) -> None:
        self.analyzer.stop()

    def set_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        placement = payload.get("placement") or ""
        turn = payload.get("turn") or "w"
        try:
            board = self.tracker.update(placement, turn)
        except InvalidPosition as exc:
            with self.lock:
                self.last_error = str(exc)
            raise

        with self.lock:
            self.pos_id += 1
            self.fen = board.fen()
            self.turn = turn
            self.source = payload.get("source") or ""
            self.exact = self.tracker.exact
            self.received = time.time()
            self.last_error = ""
            pos_id = self.pos_id

        self.analyzer.submit(board)
        return {"ok": True, "id": pos_id, "fen": self.fen, "exact": self.exact,
                "legal": board.is_valid()}

    def reset(self) -> None:
        with self.lock:
            self.tracker.reset()
            self.pos_id += 1
            self.fen = ""
            self.source = ""
            self.received = 0.0

    def state(self) -> dict[str, Any]:
        with self.lock:
            pos_id, fen, turn, source = self.pos_id, self.fen, self.turn, self.source
            exact, received, error = self.exact, self.received, self.last_error
        analysis = self.analyzer.snapshot()
        if analysis and analysis.get("id") != pos_id:
            analysis = None
        return {
            "ok": True,
            "engine": {
                "state": self.analyzer.state,
                "error": self.analyzer.error,
                "movetime": self.analyzer.movetime,
                "threads": self.analyzer.threads,
                "multipv": self.analyzer.multipv,
                "path": self.analyzer.engine_path,
            },
            "position": {
                "id": pos_id,
                "fen": fen,
                "turn": turn,
                "source": source,
                "exact": exact,
                "age": round(time.time() - received, 1) if received else None,
                "error": error,
            },
            "analysis": analysis,
            "pending": self.analyzer.pending,
        }


APP: App | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "ChessCoach/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # тишина в консоли
        pass

    def handle_one_request(self) -> None:
        # браузер закрывает keep-alive соединения — это нормально, шума в консоли не нужно
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            self.close_connection = True

    # ---------------------------------------------------------------- ответы

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code: int, body: bytes | str, ctype: str = "application/json; charset=utf-8") -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False))

    # ---------------------------------------------------------------- методы

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        assert APP is not None
        if path == "/api/state":
            self._json(200, APP.state())
        elif path == "/api/health":
            self._json(200, {"ok": True, "engine": APP.analyzer.state})
        elif path == "/api/reset":
            APP.reset()
            self._json(200, {"ok": True})
        elif path in ("/", "/index.html"):
            self._send(200, self._status_page(), "text/html; charset=utf-8")
        elif path == "/test":
            self._send(200, asset("chess_coach", "mock_board.html").read_bytes(),
                       "text/html; charset=utf-8")
        elif path == "/userscript/chess-coach.user.js":
            self._send(200, asset("userscript", "chess-coach.user.js").read_bytes(),
                       "text/javascript; charset=utf-8")
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        assert APP is not None
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "bad json"})
            return

        if path == "/api/position":
            try:
                result = APP.set_position(payload)
            except InvalidPosition as exc:
                self._json(422, {"ok": False, "error": str(exc)})
                return
            self._json(200, result)
        elif path == "/api/settings":
            APP.analyzer.configure(
                movetime=payload.get("movetime"),
                multipv=payload.get("multipv"),
                threads=payload.get("threads"),
            )
            self._json(200, {"ok": True})
        elif path == "/api/reset":
            APP.reset()
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    # ---------------------------------------------------------------- страница

    def _status_page(self) -> str:
        assert APP is not None
        state = APP.state()
        engine = state["engine"]
        pos = state["position"]
        port = self.server.server_address[1]
        return f"""<!doctype html><meta charset="utf-8">
<title>Шахматный тренер — сервер</title>
<style>
  body{{font:14px/1.6 "Segoe UI",sans-serif;background:#14161a;color:#e8eaed;padding:28px 32px;max-width:820px}}
  h2{{margin:0 0 4px;font-size:19px}} .muted{{color:#8b95a3}}
  .step{{display:flex;gap:10px;align-items:flex-start;padding:10px 14px;border-radius:8px;
         background:#1c2027;margin:8px 0}} .step b{{font-weight:600}}
  .ok{{color:#8bd498}} .bad{{color:#e5645e}} .warn{{color:#e8a33d}}
  code{{background:#262b33;padding:1px 6px;border-radius:4px;color:#8bd498}}
  a{{color:#8bd498}}
</style>
<h2>Шахматный тренер</h2>
<div class="muted" style="margin-bottom:18px">Сервер работает на порту {port}. Окно с лучшим ходом — отдельное окно «Тренер».</div>

<div class="step"><span id="s1">•</span><div><b>1. Сервер</b><br>
  <span class="muted">эта страница открылась — значит сервер уже работает</span></div></div>

<div class="step"><span id="s2">•</span><div><b>2. Движок Stockfish</b><br>
  <span class="muted" id="e2">{engine['state']} — {engine['path'] or 'не найден'}</span></div></div>

<div class="step"><span id="s3">•</span><div><b>3. Скрипт в браузере</b><br>
  <span class="muted" id="e3">позиция ещё не приходила</span></div></div>

<div class="step"><span id="s4">•</span><div><b>4. Доска</b><br>
  <span class="muted" id="e4">—</span></div></div>

<p style="margin-top:22px">
  Установить скрипт: <a href="/userscript/chess-coach.user.js">chess-coach.user.js</a>
  (нужен Tampermonkey) &nbsp;·&nbsp; тест распознавания: <a href="/test">/test</a>
</p>
<p class="muted">Эта страница обновляется сама каждые 2 секунды.</p>
<script>
async function tick() {{
  try {{
    const r = await fetch('/api/state', {{cache: 'no-store'}});
    const s = await r.json();
    const e = s.engine, p = s.position;
    mark('s1', true);
    mark('s2', e.state !== 'error' && e.state !== 'starting');
    document.getElementById('e2').textContent = e.error || (e.state + ' — ' + e.path);
    const has = !!p.fen;
    mark('s3', has);
    document.getElementById('e3').textContent = has
      ? 'позиция получена от ' + (p.source || 'браузера')
      : 'позиция ещё не приходила — скрипт не установлен или вкладка chess.com не открыта';
    mark('s4', !!s.analysis);
    document.getElementById('e4').textContent = s.analysis
      ? p.fen + '  →  ' + (s.analysis.best ? s.analysis.best.ru + ' ' + s.analysis.score.text : '—')
      : (has ? 'ждём оценку…' : '—');
  }} catch (err) {{ mark('s1', false); }}
}}
function mark(id, ok) {{
  const el = document.getElementById(id);
  el.textContent = ok ? '✓' : '✕';
  el.className = ok ? 'ok' : 'bad';
}}
tick(); setInterval(tick, 2000);
</script>"""


def serve(app: App, host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    global APP
    APP = app
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    return httpd
