"""Запуск шахматного тренера.

    python run.py              — сервер + окно с лучшим ходом
    python run.py --no-gui     — только сервер (окно не нужно)
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

from chess_coach.server import PORT, App, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Шахматный тренер: Stockfish + окно с лучшим ходом")
    parser.add_argument("--engine", help="путь к stockfish.exe (по умолчанию ищется в engine\\)")
    parser.add_argument("--movetime", type=float, default=0.5, help="секунд на ход (по умолчанию 0.5)")
    parser.add_argument("--multipv", type=int, default=1, choices=(1, 2, 3), help="сколько линий считать")
    parser.add_argument("--threads", type=int, default=None, help="потоки процессора для Stockfish")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-gui", action="store_true", help="без окна")
    parser.add_argument("--no-browser", action="store_true", help="не открывать страницу статуса")
    args = parser.parse_args(argv)

    app = App(engine_path=args.engine, movetime=args.movetime,
              threads=args.threads, multipv=args.multipv)
    if app.engine_error:
        print(f"[внимание] {app.engine_error}")

    app.start()
    try:
        serve(app, port=args.port)
    except OSError as exc:
        print(f"\n[ошибка] порт {args.port} занят: {exc}\n")
        app.stop()
        return 1

    base = f"http://127.0.0.1:{args.port}"
    print(f"Сервер: {base}   (тест доски: {base}/test)")
    print("Панель с лучшим ходом — отдельное окно. Ctrl+C для выхода.")

    if not args.no_browser:
        import webbrowser

        threading.Timer(0.6, lambda: webbrowser.open(base)).start()

    if args.no_gui:
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            app.stop()
        return 0

    try:
        from chess_coach.panel import Panel

        Panel(base).run()
    except ImportError:
        print("[ошибка] нет tkinter — запустите с --no-gui")
        return 1
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
