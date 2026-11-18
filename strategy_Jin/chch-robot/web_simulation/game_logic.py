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
    from dofbot_web import enabled, apply_move, apply_virtual  # 新入口复用规则校验。
    if enabled() and current_robot_mode() == 'simulation':  # 虚拟模式直接完成局面提交。
        return apply_virtual(best_move)  # 不留下等待实体动作的占用标志。
    if enabled() and current_robot_mode() == 'hardware':  # 不混用 STM32 和 DOFBOT 指令。
        return apply_move(best_move)  # 动作未确认前不修改正式棋局。
    _app = __import__("web_simulation.app", fromlist=["app"])
    gs = _app.game_state

    robot_command = robot_cmd.uci_to_robot_command(best_move, gs.get("board_state"))
    robot_command_text = robot_cmd.format_robot_command(robot_command)
    mode = current_robot_mode()
    command_token = ai_command_token(best_move)

    analysis = gs["ai_analysis"]
    analysis["robot_command"] = robot_command
    analysis["robot_command_text"] = robot_command_text
    analysis["robot_send_success"] = None
    analysis["robot_send_acknowledged"] = False
    analysis["robot_send_response"] = None
    analysis["robot_send_error"] = None
    analysis["robot_mode"] = mode
    analysis["ai_move_token"] = command_token
    analysis["robot_netassist_target"] = (
        f"{getattr(_app, 'ROBOT_NETWORK_HOST', '127.0.0.1')}:"
        f"{getattr(_app, 'ROBOT_NETWORK_PORT', 8086)}"
    )
    analysis["robot_log_messages"] = []
    analysis["ai_move_applied"] = False

    if gs["move_history"] and gs["move_history"][-1] == best_move:
        duplicate_result = RobotSendResult(
            success=True,
            command_text=robot_command_text,
            response="DUPLICATE_AI_MOVE_IGNORED",
        )
        gs["pending_ai_move"] = None
        gs["robot_moving"] = False
        gs["vision_pause_until"] = 0.0
        gs["vision_pause_reason"] = ""
        analysis["robot_send_success"] = True
        analysis["robot_send_acknowledged"] = True
        analysis["robot_send_response"] = duplicate_result.response
        analysis["robot_send_error"] = ""
        analysis["ai_move_applied"] = True
        analysis["robot_log_messages"] = [f"duplicate AI move ignored: {best_move}"]
        logger.warning("Ignored duplicate AI move already at history tail: %s", best_move)
        return duplicate_result

    if gs.get("last_ai_command_token") == command_token:
        duplicate_result = RobotSendResult(
            success=True,
            command_text=robot_command_text,
            response="DUPLICATE_AI_COMMAND_IGNORED",
        )
        analysis["robot_send_success"] = True
        analysis["robot_send_acknowledged"] = True
        analysis["robot_send_response"] = duplicate_result.response
        analysis["robot_send_error"] = ""
        analysis["ai_move_applied"] = True
        analysis["robot_log_messages"] = [f"duplicate AI command token ignored: {command_token}"]
        logger.warning("Ignored duplicate AI command token: %s", command_token)
        return duplicate_result

    from chro_web_tools import checked_transition  # 用规则库复核虚拟模式的 AI 走法。
    preview = checked_transition(gs["current_fen"], best_move) if mode == _app.ROBOT_MODE_SIMULATION else None  # 实体闭环留待专门改造。
    if not gs["move_history"] or gs["move_history"][-1] != best_move:
        gs["move_history"].append(best_move)
        gs["display_history"].append(robot_command_text)
        gs["turn_count"] += 1
        if preview is not None:  # 虚拟模式使用规则层的完整结果。
            gs["board_state"] = preview["board_state"]  # 同步移动棋子。
        else:  # 实体模式保留原流程，尚未实现视觉确认提交。
            web_domain.apply_uci_to_board_state(gs["board_state"], best_move)  # 不混入不完整的实体状态机。
        gs["last_ai_command_token"] = command_token

    if preview is not None:  # 虚拟模式保留准确的轮次和计数。
        gs["current_fen"] = preview["fen"]  # 提交规则层生成的局面。
    else:  # 实体模式仍需后续独立改造。
        update_current_fen()  # 保持原实体处理。
    gs["pending_ai_move"] = None
    gs["robot_moving"] = True
    gs["vision_pause_until"] = time.monotonic() + _app.ROBOT_MOVE_PAUSE_SECONDS
    gs["vision_pause_reason"] = "simulation_move" if mode == _app.ROBOT_MODE_SIMULATION else "robot_move"
    analysis["ai_move_applied"] = True

    if _app.recognizer is not None:
        sync_dynamic_baseline_to_game_state(_app.recognizer)

    logger.info(
        "AI move %s pre-applied in %s mode; vision pauses for %.0fs before controller ack",
        best_move,
        mode,
        _app.ROBOT_MOVE_PAUSE_SECONDS,
    )

    # Route through the app module so tests can patch web_app.send_robot_command_to_controller.
    if mode == _app.ROBOT_MODE_SIMULATION:
        robot_send_result = RobotSendResult(
            success=True,
            command_text=robot_command_text,
            response="RESULT:1,MODE:simulation",
        )
    else:
        robot_send_result = _app.send_robot_command_to_controller(robot_command)

    robot_acknowledged = (
        robot_send_result.success
        if mode == _app.ROBOT_MODE_SIMULATION
        else robot_send_result.motion_acknowledged
    )
    analysis["robot_send_success"] = robot_send_result.success
    analysis["robot_send_acknowledged"] = robot_acknowledged
    analysis["robot_send_response"] = robot_send_result.response
    analysis["robot_send_error"] = robot_send_result.error
    analysis["robot_log_messages"] = robot_cmd.build_robot_log_messages(
        robot_command_text,
        robot_send_result,
        robot_command,
    )

    if not robot_send_result.success:
        logger.error(
            "AI move %s is already pre-displayed, but robot send failed in %s mode: %s",
            best_move,
            mode,
            robot_send_result.error,
        )
        return robot_send_result

    if mode == _app.ROBOT_MODE_HARDWARE:
        gs["robot_moving"] = False
        gs["vision_pause_until"] = 0.0
        gs["vision_pause_reason"] = ""
        gs["awaiting_physical_baseline"] = False
        gs["last_robot_move_capture"] = robot_cmd.robot_command_is_capture(robot_command)
        gs["post_robot_guard_until"] = 0.0
        gs["robot_baseline_match_count"] = 0
        if _app.recognizer is not None:
            reset_dynamic_tracking(_app.recognizer)
            sync_dynamic_baseline_to_game_state(_app.recognizer)
        logger.info(
            "Robot controller confirmed STATE:5,RESULT:1 for %s; red-side vision recognition resumes immediately",
            best_move,
        )

    if mode == _app.ROBOT_MODE_SIMULATION:
        logger.info(
            "AI move applied in simulation mode; vision resumes in %.0fs",
            _app.ROBOT_MOVE_PAUSE_SECONDS,
        )
    else:
        logger.info("AI move applied in hardware mode; vision resumes immediately after controller ACK")
    return robot_send_result


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
