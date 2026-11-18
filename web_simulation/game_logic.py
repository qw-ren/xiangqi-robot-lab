"""
Core game-state transitions for the web simulation.

These functions mutate the shared ``game_state`` owned by the app module and
coordinate the recognizer, the robot command layer, and FEN bookkeeping. They
contain no Flask/request code so the same logic is exercised by the REST routes
and (potentially) the headless game loop.

Pure board/turn helpers live in :mod:`web_simulation.domain`; this module owns
the stateful transitions that depend on the shared app globals.
"""

import logging
import time

from web_simulation import domain as web_domain
from web_simulation import robot_cmd
from robot import RobotSendResult

logger = logging.getLogger(__name__)


def _state():
    from web_simulation import app as _app

    return _app.game_state


def current_robot_mode():
    from web_simulation import app as _app

    try:
        return web_domain.normalize_robot_mode(_state().get("robot_mode"))
    except ValueError:
        _state()["robot_mode"] = _app.ROBOT_MODE_HARDWARE
        return _app.ROBOT_MODE_HARDWARE


def current_turn_color():
    gs = _state()
    first_player = gs.get("first_player", "red")
    if len(gs["move_history"]) % 2 == 0:
        return first_player
    return web_domain.opposite_color(first_player)


def is_duplicate_player_move(move_code):
    gs = _state()
    if not move_code:
        return False
    if gs.get("last_player_move") == move_code:
        return True
    return bool(gs["move_history"] and gs["move_history"][-1] == move_code)


def ai_command_token(best_move):
    return f"{len(_state()['move_history'])}:{best_move}"


def update_current_fen():
    """Recompute ``current_fen`` from the board state and move history."""
    gs = _state()
    moves = gs["move_history"]

    if gs.get("board_state"):
        fen = web_domain.board_state_to_fen(gs["board_state"], current_turn_color())
        fen_parts = fen.split()
        initial_parts = gs["initial_fen"].split()
        initial_move_number = int(initial_parts[5]) if len(initial_parts) >= 6 else 1
        current_move_number = initial_move_number + len(moves) // 2

        if len(fen_parts) >= 6:
            fen_parts[5] = str(current_move_number)
            gs["current_fen"] = " ".join(fen_parts)
            logger.info("更新FEN: %s", gs["current_fen"])
            return

    fen_parts = gs["initial_fen"].split()
    if len(fen_parts) >= 6:
        fen_parts[1] = web_domain.color_to_turn_char(current_turn_color())
        move_number = int(fen_parts[5])
        fen_parts[5] = str(move_number + len(moves) // 2)
        gs["current_fen"] = " ".join(fen_parts)
        logger.info("更新FEN(回退): %s", gs["current_fen"])


def sync_dynamic_baseline_to_game_state(recog):
    if recog is not None:
        recog.sync_dynamic_baseline(web_domain.deserialize_board_state(_state().get("board_state") or {}))


def reset_dynamic_tracking(recog):
    if recog is not None and hasattr(recog, "reset_dynamic_tracking"):
        recog.reset_dynamic_tracking()


def robot_physical_baseline_status(observed_board):
    """
    Check whether the physically moved board matches the expected baseline.

    Returns ``(ready, message)``. The expected board comes from the shared game
    state; the match counter lives there too so consecutive stable frames are
    required before the baseline is locked.
    """
    _app = __import__("web_simulation.app", fromlist=["app"])

    required = int(getattr(_app, "ROBOT_BASELINE_MATCH_REQUIRED", 3))
    expected_board = web_domain.deserialize_board_state(_app.game_state.get("board_state") or {})
    observed_board = dict(observed_board or {})
    expected_positions = set(expected_board.keys())
    observed_positions = set(observed_board.keys())
    missing = expected_positions - observed_positions
    extra = observed_positions - expected_positions

    if missing or extra:
        _app.game_state["robot_baseline_match_count"] = 0
        return False, (
            "等待机械臂落子后棋盘稳定: "
            f"当前识别{len(observed_positions)}/期望{len(expected_positions)}，"
            f"缺失{len(missing)}，多出{len(extra)}"
        )

    match_count = _app.game_state.get("robot_baseline_match_count", 0) + 1
    _app.game_state["robot_baseline_match_count"] = match_count
    if match_count < required:
        return False, (
            "等待机械臂落子后棋盘连续稳定: "
            f"{match_count}/{required}"
        )

    return True, "机械臂落子后的真实棋盘基线已锁定，等待红方走子"


def apply_ai_best_move(best_move):
    """功能：将引擎 best_move（ICCS）交给统一网页/串口出口，只计算和提交一次。"""
    from web_simulation.dofbot import apply_move, apply_virtual
    if current_robot_mode() == 'simulation':
        return apply_virtual(best_move)
    return apply_move(best_move)


def dynamic_state_payload(event="paused", stable=False, message="", move=None):
    _app = __import__("web_simulation.app", fromlist=["app"])
    gs = _app.game_state
    pause_until = gs.get("vision_pause_until", 0.0) or 0.0
    pause_remaining = max(0.0, pause_until - time.monotonic())

    return {
        "success": True,
        "event": event,
        "stable": stable,
        "message": message,
        "move": move,
        "board_state": dict(gs.get("board_state") or {}),
        "fen": gs.get("current_fen"),
        "current_fen": gs.get("current_fen"),
        "piece_count": len(gs.get("board_state") or {}),
        "move_history": gs["move_history"],
        "display_history": gs["display_history"],
        "turn_count": gs["turn_count"],
        "is_game_running": gs["is_game_running"],
        "ai_color": gs["ai_color"],
        "player_color": gs["player_color"],
        "first_player": gs["first_player"],
        "robot_mode": gs.get("robot_mode", _app.ROBOT_MODE_HARDWARE),
        "current_turn": current_turn_color(),
        "vision_paused": pause_remaining > 0,
        "vision_pause_remaining": round(pause_remaining, 1),
    }
