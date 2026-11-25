"""Durable confirmed game state and read-only display data."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from xq_utils.rules import board_from_fen, checked_transition


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GameJournal:
    def __init__(self, path: Path, state=None):
        self.path = path
        self.state = state if state is not None else {
            "version": 1, "initial_fen": None, "fen": None, "moves": [],
            "captures": 0, "pending": None, "phase": "setup", "detail": "", "result": None,
        }

    @classmethod
    def load(cls, path: Path):
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["version"] != 1 or not state["fen"] or state["result"]:
            raise ValueError("存档没有可继续的对局")
        board_from_fen(state["fen"])
        if not 0 <= state["captures"] <= 16:
            raise ValueError("存档中的吃子区计数无效")
        if state["pending"]:
            checked_transition(state["fen"], state["pending"]["move"])
        return cls(path, state)

    def update(self, **changes):
        if self.path.exists() and all(self.state.get(key) == value for key, value in changes.items()):
            return
        state = dict(self.state, **changes, updated_at=now())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as tmp:
                temporary = Path(tmp.name)
                json.dump(state, tmp, ensure_ascii=False)
                tmp.flush()
                os.fsync(tmp.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.state = state

    def status(self, phase: str, detail: str = ""):
        self.update(phase=phase, detail=detail)

    def confirm(self, *moves: str, starting_fen=None, captures=None):
        fen = starting_fen or self.state["fen"]
        history = list(self.state["moves"])
        for move in moves:
            side = fen.split()[1]
            plan = checked_transition(fen, move)
            row, col = plan["target"]
            piece = plan["board_state"][f"{col},{row}"]
            fen = plan["fen"]
            history.append(dict(side=side, move=move, piece=piece, capture=plan["capture"],
                                fen=fen, confirmed_at=now()))
        changes = dict(fen=fen, moves=history, pending=None, detail="")
        if starting_fen is not None:
            changes.update(initial_fen=starting_fen, captures=captures)
        self.update(**changes)
