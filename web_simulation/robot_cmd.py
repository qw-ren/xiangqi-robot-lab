"""
Robot command translation and transport helpers.

These wrap the active STM32 five-value protocol (``robot.protocol``) and the
shared persistent TCP client owned by the app. They are kept free of Flask and
request concerns so the game loop and the REST routes can both use them.
"""

import logging

import config
from robot import (
    RobotArmCommand,
    RobotHomingCommand,
    RobotSendResult,
    uci_to_arm_command,
)

logger = logging.getLogger(__name__)


def get_robot_board_config():
    """Build the board->arm coordinate config from project settings."""
    return __import__("robot").protocol.BoardToArmConfig(
        origin_x=getattr(config, "ROBOT_COMMAND_ORIGIN_X", 0),
        origin_y=getattr(config, "ROBOT_COMMAND_ORIGIN_Y", 0),
        file_spacing_mm=getattr(config, "ROBOT_COMMAND_FILE_SPACING_MM", 34),
        rank_spacing_mm=getattr(config, "ROBOT_COMMAND_RANK_SPACING_MM", 30),
        river_spacing_mm=getattr(config, "ROBOT_COMMAND_RIVER_SPACING_MM", 32),
    )


def robot_network_target():
    return (
        getattr(config, "ROBOT_NETWORK_HOST", "192.168.0.102"),
        int(getattr(config, "ROBOT_NETWORK_PORT", 8086)),
    )


def robot_network_timeout():
    return getattr(config, "ROBOT_NETWORK_TIMEOUT", 1.0)


def robot_command_timeout_for_command(command):
    if robot_command_is_capture(command):
        return float(
            getattr(
                config,
                "ROBOT_CAPTURE_COMMAND_TIMEOUT",
                max(getattr(config, "ROBOT_COMMAND_TIMEOUT", 60.0), 120.0),
            )
        )
    return float(
        getattr(
            config,
            "ROBOT_NORMAL_COMMAND_TIMEOUT",
            getattr(
                config,
                "ROBOT_COMMAND_TIMEOUT",
                max(robot_network_timeout(), _move_pause_seconds() + 5.0),
            ),
        )
    )


def _move_pause_seconds() -> float:
    return float(getattr(config, "ROBOT_SIMULATED_MOVE_SECONDS", 15.0))


def uci_to_robot_command(uci_move, board_state=None):
    """Convert UCI to the STM32 five-value command [startX,startY,endX,endY,signal]."""
    try:
        return uci_to_arm_command(
            uci_move,
            board_state=board_state,
            config=get_robot_board_config(),
        ).to_tuple()
    except ValueError as exc:
        logger.warning("Invalid UCI move for robot command: %s, error=%s", uci_move, exc)
        return None


def send_robot_command_to_controller(command):
    """旧 TCP 动作接口已停用；本项目只通过统一串口出口发送 ICCS 走棋消息。"""
    return RobotSendResult(success=False, command_text='', error='TCP 运动/归零已移除，请使用走棋串口')


def probe_robot_controller():
    """旧 TCP 动作接口已停用；本项目只通过统一串口出口发送 ICCS 走棋消息。"""
    return RobotSendResult(success=False, command_text='', error='TCP 运动/归零已移除，请使用走棋串口')


def send_homing_command_to_controller():
    """旧 TCP 动作接口已停用；本项目只通过统一串口出口发送 ICCS 走棋消息。"""
    return RobotSendResult(success=False, command_text='', error='TCP 运动/归零已移除，请使用走棋串口')


def build_robot_log_messages(robot_command_text, send_result, robot_command=None):
    target = f"{getattr(config, 'ROBOT_NETWORK_HOST', '127.0.0.1')}:{getattr(config, 'ROBOT_NETWORK_PORT', 8086)}"
    messages = [
        f"STM32 target: {target}",
        f"five-value command: {robot_command_text or 'none'}",
        f"sent payload: {send_result.command_text or robot_command_text or 'none'}",
    ]
    if robot_command is not None:
        messages.append(f"command timeout: {robot_command_timeout_for_command(robot_command):.1f}s")

    if send_result.success:
        if send_result.response:
            messages.append(f"controller response: {send_result.response}")
        else:
            messages.append("send result: sent")
    else:
        messages.append(f"send result: failed, {send_result.error}")

    return messages


def format_robot_command(command):
    if hasattr(command, "to_wire"):
        return command.to_wire()
    return ",".join(str(v) for v in command) if command else ""


def robot_command_is_capture(command):
    try:
        if isinstance(command, RobotArmCommand):
            return command.signal == 1
        return len(command or []) >= 5 and int(command[4]) == 1
    except (TypeError, ValueError):
        return False


def robot_settle_seconds_for_command(command):
    settle_capture = float(getattr(config, "ROBOT_CAPTURE_SETTLE_SECONDS", 0.0))
    settle_normal = float(getattr(config, "ROBOT_NORMAL_SETTLE_SECONDS", 0.0))
    return settle_capture if robot_command_is_capture(command) else settle_normal
