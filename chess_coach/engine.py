"""Stockfish: поиск бинарника, фоновая оценка позиции, форматирование оценки."""

from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

import chess
import chess.engine

RU_PIECES = {"K": "Кр", "Q": "Ф", "R": "Л", "B": "С", "N": "К"}


# ---------------------------------------------------------------- утилиты


def ru_san(san: str) -> str:
    """SAN в русской нотации: Nf3 -> Кf3, O-O -> 0-0, e8=Q -> e8=Ф."""
    out = re.sub(r"^([KQRBN])", lambda m: RU_PIECES[m.group(1)], san)
    out = re.sub(r"=([KQRBN])", lambda m: "=" + RU_PIECES[m.group(1)], out)
    return out.replace("O-O-O", "0-0-0").replace("O-O", "0-0")


def eval_text(cp: int | None, mate: int | None) -> str:
    """'мат в 3' / '+1.24' (положительное — лучше белым)."""
    if mate is not None:
        if mate > 0:
            return f"мат в {mate}"
        return f"мат у чёрных в {abs(mate)}"
    if cp is None:
        return "?"
    if abs(cp) > 9000:  # табличная база / отсутствие материала
        return "ничья"
    return f"{cp / 100:+.2f}".replace("-", "−")


def eval_number(cp: int | None, mate: int | None) -> float:
    """Число для полоски оценки: +1.0 = пешка вверх за белых."""
    if mate is not None:
        return 30.0 if mate > 0 else -30.0
    if cp is None:
        return 0.0
    return max(-30.0, min(30.0, cp / 100.0))


def app_dir() -> Path:
    """Папка приложения: рядом с .exe в собранной версии, иначе корень проекта."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_engine(explicit: str | None = None) -> str:
    """Ищет исполняемый файл Stockfish."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("CHESS_COACH_ENGINE"):
        candidates.append(Path(os.environ["CHESS_COACH_ENGINE"]))

    engine_dir = app_dir() / "engine"
    if engine_dir.is_dir():
        candidates.extend(sorted(engine_dir.rglob("stockfish*.exe")))
        candidates.extend(sorted(engine_dir.rglob("stockfish")))

    for path in candidates:
        if path.is_file():
            return str(path)

    from shutil import which

    found = which("stockfish")
    if found:
        return found
    raise FileNotFoundError(
        "Не найден Stockfish. Положите stockfish.exe в папку engine\\ "
        "или укажите путь: python run.py --engine C:\\path\\stockfish.exe"
    )


# ---------------------------------------------------------------- анализатор


class Analyzer:
    """Один поток-воркер: всегда считает только самую свежую позицию."""

    def __init__(
        self,
        engine_path: str,
        movetime: float = 0.5,
        threads: int | None = None,
        hash_mb: int = 256,
        multipv: int = 1,
    ) -> None:
        self.engine_path = engine_path
        self.movetime = movetime
        self.threads = threads or max(1, (os.cpu_count() or 4) - 1)
        self.hash_mb = hash_mb
        self.multipv = multipv

        self._cond = threading.Condition()
        self._job: tuple[int, chess.Board, float, int] | None = None
        self._next_id = 0
        self._running = True
        self._dirty = True

        self._lock = threading.Lock()
        self._result: dict[str, Any] | None = None
        self.state = "starting"
        self.error = ""
        self._engine: chess.engine.SimpleEngine | None = None

        self._thread = threading.Thread(target=self._run, name="stockfish", daemon=True)

    # ------------------------------------------------------------ lifecycle

    def start(self) -> "Analyzer":
        self._thread.start()
        return self

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        self._thread.join(timeout=3)

    # ------------------------------------------------------------ настройки

    def configure(self, *, movetime: float | None = None, multipv: int | None = None,
                  threads: int | None = None) -> None:
        with self._cond:
            if movetime is not None:
                self.movetime = max(0.05, min(10.0, float(movetime)))
            if multipv is not None:
                self.multipv = max(1, min(5, int(multipv)))
            if threads is not None:
                self.threads = max(1, min(64, int(threads)))
            self._dirty = True
            self._cond.notify_all()

    # ------------------------------------------------------------ очередь

    def submit(self, board: chess.Board) -> int:
        with self._cond:
            self._next_id += 1
            self._job = (self._next_id, board.copy(), self.movetime, self.multipv)
            self._cond.notify_all()
            return self._next_id

    @property
    def pending(self) -> bool:
        with self._cond:
            return self._job is not None and self.state != "error"

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._result) if self._result else None

    # ------------------------------------------------------------ воркер

    def _run(self) -> None:
        if not self.engine_path:
            self.state = "error"
            self.error = "не найден Stockfish: положите stockfish.exe в папку engine\\"
            return
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            self._configure_engine()
        except Exception as exc:  # noqa: BLE001
            self.state = "error"
            self.error = f"движок не запустился: {exc}"
            return

        self.state = "idle"
        try:
            while self._running:
                with self._cond:
                    while self._job is None and self._running:
                        self._cond.wait(timeout=0.5)
                    if not self._running:
                        break
                    if self._dirty:
                        self._apply_options()
                    job = self._job
                    self._job = None

                if job is None:
                    continue
                self._analyze(*job)
        finally:
            try:
                if self._engine is not None:
                    self._engine.quit()
            except Exception:  # noqa: BLE001
                pass
            self.state = "stopped"

    def _configure_engine(self) -> None:
        assert self._engine is not None
        # MultiPV python-chess выставляет сам в каждом запросе analysis(multipv=...)
        self._engine.configure({"Threads": self.threads, "Hash": self.hash_mb})

    def _apply_options(self) -> None:
        assert self._engine is not None
        try:
            self._engine.configure({"Threads": self.threads, "Hash": self.hash_mb})
        except chess.engine.EngineError:
            pass
        self._dirty = False

    def _analyze(self, job_id: int, board: chess.Board, movetime: float, multipv: int) -> None:
        assert self._engine is not None
        self.state = "analyzing"
        if self.pending:
            return
        # python-chess отдаёт отдельный info на каждую линию MultiPV,
        # поэтому последние значения складываем по номеру линии.
        latest: dict[int, dict[str, Any]] = {}
        try:
            with self._engine.analysis(
                board, chess.engine.Limit(time=movetime), multipv=multipv
            ) as stream:
                for info in stream:
                    if self.pending:
                        break
                    if "score" not in info:
                        continue
                    latest[info.get("multipv", 1)] = info
                    self._publish(job_id, board, latest, done=False)
            if latest:
                self._publish(job_id, board, latest, done=True)
        except Exception as exc:  # noqa: BLE001
            self.error = f"ошибка анализа: {exc}"
        finally:
            self.state = "idle"

    def _publish(self, job_id: int, board: chess.Board, latest: dict[int, dict[str, Any]],
                 done: bool) -> None:
        lines = []
        for rank in sorted(latest):
            info = latest[rank]
            pv = info.get("pv") or []
            if not pv:
                continue
            white = info["score"].white()
            cp = None if white.is_mate() else white.score()
            mate = white.mate() if white.is_mate() else None
            san = _san(board, pv[0])
            pv_san = _pv_san(board, pv[:10])
            lines.append({
                "rank": rank,
                "score": eval_text(cp, mate),
                "cp": cp,
                "mate": mate,
                "uci": pv[0].uci(),
                "from": chess.square_name(pv[0].from_square),
                "to": chess.square_name(pv[0].to_square),
                "san": san,
                "ru": ru_san(san),
                "pv": pv_san,
                "pv_ru": [ru_san(m) for m in pv_san],
            })

        if not lines:
            return
        head = latest[min(latest)]
        best = lines[0]
        result = {
            "id": job_id,
            "depth": head.get("depth"),
            "seldepth": head.get("seldepth"),
            "nodes": head.get("nodes"),
            "nps": head.get("nps"),
            "time": head.get("time"),
            "score": {"cp": best["cp"], "mate": best["mate"], "text": best["score"]},
            "best": best,
            "lines": lines,
            "done": done,
        }
        with self._lock:
            self._result = result


def _san(board: chess.Board, move: chess.Move) -> str:
    try:
        return board.san(move)
    except Exception:  # noqa: BLE001
        return move.uci()


def _pv_san(board: chess.Board, moves: list[chess.Move]) -> list[str]:
    """SAN для всей линии: каждый ход применяется к копии доски по очереди."""
    probe = board.copy(stack=False)
    out: list[str] = []
    for move in moves:
        try:
            out.append(probe.san(move))
            probe.push(move)
        except Exception:  # noqa: BLE001
            out.append(move.uci())
            break
    return out
