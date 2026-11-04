"""Сборка FEN из расстановки фигур, прочитанной со страницы.

Браузер присылает только расстановку (поле вида "rnbqkbnr/pppppppp/...")
и того, чей ход. Права на рокировку и поле взятия на проходе по DOM
не достать, поэтому класс Tracker делает две вещи:

1. Пытается "продолжить" предыдущую позицию: если новая расстановка
   получается из старой ровно одним легальным ходом, мы берём точное
   состояние (включая права на рокировку и e.p.).
2. Если продолжить не удалось (первая позиция, новый ход соперника после
   паузы, рассинхрон) — строит позицию с правами на рокировку по эвристике
   "король и ладья на своих начальных полях".
"""

from __future__ import annotations

import chess

_KNIGHTS = "pnbrqk"


class InvalidPosition(ValueError):
    """Расстановка не похожа на настоящую шахматную позицию."""


def parse_placement(placement: str, turn: str = "w") -> chess.Board:
    placement = (placement or "").strip()
    rows = placement.split("/")
    if len(rows) != 8:
        raise InvalidPosition("в расстановке должно быть 8 рядов")
    if turn not in ("w", "b"):
        raise InvalidPosition("неизвестно, чей ход")

    for row in rows:
        files = 0
        for ch in row:
            if ch.isdigit():
                files += int(ch)
            elif ch.lower() in _KNIGHTS:
                files += 1
            else:
                raise InvalidPosition(f"непонятный символ в расстановке: {ch!r}")
        if files != 8:
            raise InvalidPosition("в ряду должно быть 8 полей")

    board = chess.Board(f"{placement} {turn} - - 0 1")
    _validate(board)
    return board


def _validate(board: chess.Board) -> None:
    kings = [sq for sq in chess.SQUARES if board.piece_at(sq) and board.piece_at(sq).piece_type == chess.KING]
    whites = [sq for sq in chess.SQUARES if board.piece_at(sq) and board.piece_at(sq).color == chess.WHITE]
    blacks = [sq for sq in chess.SQUARES if board.piece_at(sq) and board.piece_at(sq).color == chess.BLACK]

    if len(kings) != 2:
        raise InvalidPosition("на доске должно быть ровно два короля")
    if len(whites) > 16 or len(blacks) > 16:
        raise InvalidPosition("слишком много фигур")
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece and piece.piece_type == chess.PAWN and chess.square_rank(sq) in (0, 7):
            raise InvalidPosition("пешка не может стоять на первой или восьмой линии")


def _with_heuristic_castling(board: chess.Board) -> chess.Board:
    """Права на рокировку по принципу "король и ладья ещё на своих местах"."""
    rights = ""
    for color, rank in ((chess.WHITE, "1"), (chess.BLACK, "8")):
        king = board.piece_at(chess.parse_square("e" + rank))
        if not king or king.piece_type != chess.KING or king.color != color:
            continue
        for file, letter in (("h", "K"), ("a", "Q")):
            rook = board.piece_at(chess.parse_square(file + rank))
            if rook and rook.piece_type == chess.ROOK and rook.color == color:
                rights += letter if color == chess.WHITE else letter.lower()
    if rights:
        board.set_castling_fen(rights)
    return board


class Tracker:
    """Хранит актуальную позицию и уточняет её по разнице расстановок."""

    def __init__(self) -> None:
        self.board: chess.Board | None = None
        self.exact = False  # права на рокировку/e.p. восстановлены точно

    def reset(self) -> None:
        self.board = None
        self.exact = False

    def update(self, placement: str, turn: str) -> chess.Board:
        target = parse_placement(placement, turn)

        if self.board is not None:
            if self.board.fen() == target.fen():
                self.exact = True
                return self.board

            moved = self._continue_from_previous(target)
            if moved is not None:
                self.board = moved
                self.exact = True
                return self.board

        board = _with_heuristic_castling(target)
        self.board = board
        self.exact = False
        return board

    def _continue_from_previous(self, target: chess.Board) -> chess.Board | None:
        assert self.board is not None
        previous = self.board
        wanted_placement = target.board_fen()
        turn = target.turn
        for move in previous.legal_moves:
            probe = previous.copy(stack=False)
            probe.push(move)
            if probe.turn == turn and probe.board_fen() == wanted_placement:
                continuation = previous.copy()
                continuation.push(move)
                return continuation
        return None
