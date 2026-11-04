"""Маленькое окно поверх всех: лучший ход, оценка, линия."""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

BG = "#14161a"
BG_SOFT = "#1c2027"
FG = "#e8eaed"
MUTED = "#8b95a3"
ACCENT = "#8bd498"
WARN = "#e8a33d"
BAD = "#e5645e"


class Poller(threading.Thread):
    """Тянет /api/state в фоне, чтобы окно не подвисало."""

    def __init__(self, url: str, interval: float = 0.15) -> None:
        super().__init__(daemon=True, name="poller")
        self.url = url
        self.interval = interval
        self._stop = False
        self.latest: dict | None = None
        self.online = False
        self.lock = threading.Lock()

    def run(self) -> None:
        while not self._stop:
            started = time.time()
            try:
                with urllib.request.urlopen(self.url, timeout=1.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                with self.lock:
                    self.latest = data
                    self.online = True
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                with self.lock:
                    self.online = False
            elapsed = time.time() - started
            time.sleep(max(0.03, self.interval - elapsed))

    def stop(self) -> None:
        self._stop = True


class Panel:
    def __init__(self, base: str = "http://127.0.0.1:8765") -> None:
        self.base = base.rstrip("/")
        self.poller = Poller(self.base + "/api/state")
        self.poller.start()

        self.root = tk.Tk()
        self.root.title("Тренер")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.minsize(360, 150)
        self.root.geometry(self._initial_geometry())

        self.movetime = tk.DoubleVar(value=0.5)
        self.lines = tk.IntVar(value=1)

        self._build()
        self.root.after(120, self._tick)

    # ------------------------------------------------------------- геометрия

    def _initial_geometry(self) -> str:
        width, height = 400, 186
        screen_w = self.root.winfo_screenwidth()
        x = max(20, screen_w - width - 40)
        return f"{width}x{height}+{x}+70"

    # ------------------------------------------------------------- вид

    def _build(self) -> None:
        pad = {"padx": 14}
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", pady=(10, 0), **pad)

        self.dot = tk.Label(header, text="●", bg=BG, fg=MUTED, font=("Segoe UI", 10))
        self.dot.pack(side="left")
        self.status = tk.Label(header, text="запуск…", bg=BG, fg=MUTED,
                               font=("Segoe UI", 9), anchor="w")
        self.status.pack(side="left", padx=(6, 0))
        self.turn = tk.Label(header, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.turn.pack(side="right")

        self.move = tk.Label(self.root, text="—", bg=BG, fg=ACCENT,
                             font=("Segoe UI", 34, "bold"), anchor="w")
        self.move.pack(fill="x", padx=12)

        self.evaluation = tk.Label(self.root, text="", bg=BG, fg=FG,
                                   font=("Segoe UI", 11), anchor="w")
        self.evaluation.pack(fill="x", **pad)

        self.line = tk.Label(self.root, text="", bg=BG, fg=MUTED, justify="left",
                             font=("Segoe UI", 9), anchor="w", wraplength=372)
        self.line.pack(fill="x", **pad)

        self.bar = tk.Canvas(self.root, height=5, bg=BG_SOFT, highlightthickness=0, bd=0)
        self.bar.pack(fill="x", side="bottom", pady=(8, 0))

        self.menu = tk.Menu(self.root, tearoff=0)
        speed = tk.Menu(self.menu, tearoff=0)
        for value, label in ((0.15, "0.15 с (быстро)"), (0.3, "0.3 с"),
                             (0.5, "0.5 с (норма)"), (1.0, "1 с"), (2.0, "2 с")):
            speed.add_radiobutton(label=label, value=value, variable=self.movetime,
                                  command=self._apply_settings)
        self.menu.add_cascade(label="Скорость анализа", menu=speed)

        lines = tk.Menu(self.menu, tearoff=0)
        for value in (1, 2, 3):
            lines.add_radiobutton(label=f"{value}", value=value, variable=self.lines,
                                  command=self._apply_settings)
        self.menu.add_cascade(label="Линий анализа", menu=lines)
        self.menu.add_separator()
        self.menu.add_command(label="Скопировать FEN", command=self._copy_fen)
        self.menu.add_command(label="Сбросить позицию", command=self._reset)
        self.menu.add_separator()
        self.menu.add_command(label="Выход", command=self._quit)

        for widget in (self.root, self.move, self.evaluation, self.line, self.status):
            widget.bind("<Button-3>", self._show_menu)

        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------- меню

    def _show_menu(self, event: tk.Event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _apply_settings(self) -> None:
        body = json.dumps({"movetime": self.movetime.get(), "multipv": self.lines.get()})
        self._post("/api/settings", body)

    def _copy_fen(self) -> None:
        fen = (self._last.get("position", {}) or {}).get("fen", "")
        self.root.clipboard_clear()
        self.root.clipboard_append(fen)
        self.status.configure(text="FEN скопирован", fg=ACCENT)

    def _reset(self) -> None:
        self._post("/api/reset", "{}")

    def _post(self, path: str, body: str) -> None:
        def work() -> None:
            request = urllib.request.Request(
                self.base + path, data=body.encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(request, timeout=1.0).read()
            except (urllib.error.URLError, TimeoutError, OSError):
                pass

        threading.Thread(target=work, daemon=True).start()

    def _quit(self) -> None:
        self.poller.stop()
        self.root.destroy()

    # ------------------------------------------------------------- отрисовка

    _last: dict = {}

    def _tick(self) -> None:
        with self.poller.lock:
            data = self.poller.latest
            online = self.poller.online

        if not online:
            self._render_offline()
        elif data:
            self._render(data)

        self.root.after(120, self._tick)

    def _render_offline(self) -> None:
        self.dot.configure(fg=BAD)
        self.status.configure(text="нет связи с сервером", fg=BAD)
        self.move.configure(text="—", fg=MUTED)
        self.evaluation.configure(text="")
        self.line.configure(text="")
        self.turn.configure(text="")
        self._draw_bar(0.0)

    def _render(self, data: dict) -> None:
        self._last = data
        engine = data.get("engine", {})
        position = data.get("position", {})
        analysis = data.get("analysis")

        font_settings = engine.get("movetime", 0.5)
        self.movetime.set(font_settings)
        self.lines.set(engine.get("multipv", 1))

        if engine.get("error"):
            self.dot.configure(fg=BAD)
            self.status.configure(text=engine["error"][:60], fg=BAD)
        elif engine.get("state") in ("starting", "error"):
            self.dot.configure(fg=WARN)
            self.status.configure(text="движок запускается…", fg=WARN)
        else:
            self.dot.configure(fg=ACCENT)

        source = position.get("source") or "браузер"
        turn = position.get("turn")
        self.turn.configure(text={"w": "ход белых", "b": "ход чёрных"}.get(turn, ""))

        if not position.get("fen"):
            if not engine.get("error"):
                self.status.configure(text="ожидание позиции из браузера", fg=MUTED)
            self.move.configure(text="—", fg=MUTED)
            self.evaluation.configure(text="")
            self.line.configure(text="проверьте, что в браузере включён скрипт", )
            self._draw_bar(0.0)
            return

        if position.get("error"):
            self.status.configure(text="не удалось прочитать позицию", fg=BAD)
        elif position.get("age") is not None and position["age"] > 8:
            self.status.configure(text=f"{source} · нет новых ходов {int(position['age'])} с", fg=WARN)
        else:
            self.status.configure(text=f"{source} · живая позиция", fg=ACCENT)

        if not analysis:
            self.move.configure(text="думаю…" if not engine.get("error") else "—",
                                fg=MUTED)
            self.evaluation.configure(text="")
            self.line.configure(text=position.get("fen", "")[:80])
            self._draw_bar(0.0)
            return

        best = analysis.get("best") or {}
        score = analysis.get("score") or {}
        depth = analysis.get("depth") or 0
        suffix = "" if analysis.get("done") else "…"

        self.move.configure(text=(best.get("ru") or "—") + suffix, fg=ACCENT)
        self.evaluation.configure(
            text=f"{score.get('text', '?')}   ·   глубина {depth}",
            fg=FG,
        )
        pv_ru = (best.get("pv_ru") or [])[:6]
        self.line.configure(text=" ".join(pv_ru) if pv_ru else best.get("uci", ""))
        self._draw_bar(analysis)

    def _draw_bar(self, analysis: dict | float) -> None:
        self.bar.delete("all")
        width = max(self.bar.winfo_width(), 360)
        if isinstance(analysis, dict):
            score = analysis.get("score") or {}
            cp, mate = score.get("cp"), score.get("mate")
            if mate is not None:
                value = 30.0 if mate > 0 else -30.0
            else:
                value = max(-8.0, min(8.0, (cp or 0) / 100.0))
        else:
            value = 0.0
        share = 0.5 + (value / 16.0)
        share = max(0.04, min(0.96, share))
        self.bar.create_rectangle(0, 0, int(width * share), 6, fill="#e8eaed", width=0)
        self.bar.create_rectangle(int(width * share), 0, width, 6, fill="#3a3f47", width=0)

    def run(self) -> None:
        self.root.mainloop()
