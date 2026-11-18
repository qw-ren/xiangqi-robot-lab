"""网页与走棋串口一致性测试；全部使用假串口，不连接或移动实机。"""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from robot.dofbot_adapter import DofbotController
from web_simulation import app as web_app
from web_simulation import dofbot
from xq_utils.rules import START_FEN, board_from_fen, checked_transition


class FakeSerial:
    """只记录字节；short/error 模拟驱动短写或断连。"""

    def __init__(self, port='TEST', baud=115200, **kwargs):
        self.port, self.baudrate, self.is_open = port, baud, True
        self.writes = []
        self.short = self.error = False

    def write(self, wire):
        self.writes.append(wire)
        if self.error:
            raise OSError('disconnected')
        return len(wire) - 1 if self.short else len(wire)

    def close(self):
        self.is_open = False


@pytest.fixture
def game():
    """隔离共享网页棋局、配置与连接，不影响其余视觉测试。"""
    old_state = copy.deepcopy(web_app.game_state)
    old_ctl, old_recog = dofbot.controller, web_app.recognizer
    old_backend = web_app.config.ROBOT_BACKEND
    old_port, old_baud = web_app.config.ROBOT_SERIAL_PORT, web_app.config.ROBOT_SERIAL_BAUD
    old_preview = getattr(web_app.config, 'VISION_ONLY', False)
    web_app.config.VISION_ONLY = False
    web_app.config.ROBOT_BACKEND = 'serial'
    ctl = DofbotController('serial')
    ctl.serial_port = FakeSerial()
    ctl.connected = True
    dofbot.controller, web_app.recognizer = ctl, None
    web_app.game_state.update(
        initial_fen=START_FEN, current_fen=START_FEN, board_state=board_from_fen(START_FEN),
        move_history=[], display_history=[], turn_count=0, ai_thinking=False,
        is_game_running=True, ai_color='red', player_color='black', first_player='red',
        robot_mode='simulation', robot_moving=False, pending_dofbot=None,
        awaiting_initial_board=False, awaiting_physical_baseline=False,
        game_id='test-game', ai_analysis={}, vision_feedback=False, last_dofbot_token=None)
    try:
        yield web_app.game_state, ctl, web_app.app.test_client()
    finally:
        ctl.close()
        web_app.game_state.clear()
        web_app.game_state.update(old_state)
        dofbot.controller, web_app.recognizer = old_ctl, old_recog
        web_app.config.ROBOT_BACKEND = old_backend
        web_app.config.ROBOT_SERIAL_PORT, web_app.config.ROBOT_SERIAL_BAUD = old_port, old_baud
        web_app.config.VISION_ONLY = old_preview


def test_web_decision_and_serial_move_are_identical(game):
    gs, ctl, client = game
    result = web_app.apply_ai_best_move('b2e2')
    assert result.success
    payload = json.loads(ctl.serial_port.writes[0])
    assert payload['move'] == gs['move_history'][-1] == 'b2e2'
    assert payload['from'] == {'row': 7, 'col': 1}
    assert payload['to'] == {'row': 7, 'col': 4}
    assert payload['piece'] == 'C' and payload['capture'] is None
    assert payload['expected_fen'] == gs['current_fen']
    assert payload['pieces'] == gs['ai_analysis']['all_pieces']
    assert len(payload['pieces']) == 32
    assert payload['command_id'] == 'test-game:0:b2e2'
    assert payload['before_fen'] == START_FEN
    assert gs['turn_count'] == 1
    state = client.get('/api/status').get_json()['state']
    assert state['board_state'] == checked_transition(START_FEN, payload['move'])['board_state']
    assert gs['ai_analysis']['robot_send_acknowledged'] is False


@pytest.mark.parametrize('hardware', [False, True])
def test_one_decision_only_writes_once_and_never_calls_motion(game, hardware):
    gs, ctl, _ = game
    gs['robot_mode'] = 'hardware' if hardware else 'simulation'
    with patch.object(ctl, 'execute', side_effect=AssertionError('motion called')):
        web_app.apply_ai_best_move('b2e2')
    with pytest.raises(ValueError):
        web_app.apply_ai_best_move('b2e2')
    assert len(ctl.serial_port.writes) == 1
    assert len(gs['move_history']) == 1


@pytest.mark.parametrize('failure', ['short', 'error', 'closed'])
def test_failed_send_does_not_commit_web_or_retry(game, failure):
    gs, ctl, _ = game
    if failure == 'closed':
        ctl.serial_port.close()
    else:
        setattr(ctl.serial_port, failure, True)
    result = web_app.apply_ai_best_move('b2e2')
    assert not result.success
    assert gs['current_fen'] == START_FEN and gs['move_history'] == []
    assert gs['ai_analysis']['ai_move_applied'] is False
    with pytest.raises(ValueError):
        web_app.apply_ai_best_move('b2e2')
    assert len(ctl.serial_port.writes) <= 1


def test_illegal_decision_sends_nothing(game):
    gs, ctl, _ = game
    with pytest.raises(ValueError):
        web_app.apply_ai_best_move('a0a9')
    assert not ctl.serial_port.writes and not gs['move_history']


def test_late_search_after_game_stop_cannot_send(game):
    gs, ctl, _ = game
    gs['is_game_running'] = False
    with pytest.raises(ValueError):
        web_app.apply_ai_best_move('b2e2')
    assert not ctl.serial_port.writes


def test_capture_payload_contains_surviving_board_and_captured_piece(game):
    gs, ctl, _ = game
    result = web_app.apply_ai_best_move('b2b9')
    assert result.success
    payload = json.loads(ctl.serial_port.writes[0])
    assert payload['capture'] == 'n'
    assert len(payload['pieces']) == 31
    assert payload['pieces'] == gs['ai_analysis']['all_pieces']
    assert gs['board_state']['1,0'] == 'C'


def test_vision_confirmation_does_not_append_the_same_move_twice(game):
    gs, ctl, _ = game
    gs.update(robot_mode='hardware', vision_feedback=True)
    web_app.apply_ai_best_move('b2e2')
    expected = dict(gs['board_state'])
    assert gs['pending_dofbot']['committed'] is True
    assert gs['move_history'] == ['b2e2']  # 决策已经同步网页，但实体仍待实测。
    assert dofbot.accept_observation(board_from_fen(START_FEN), True)[0] is False
    assert dofbot.accept_observation(expected, False)[0] is False
    assert dofbot.accept_observation(expected, True)[0] is False
    assert dofbot.accept_observation(expected, True)[0] is False
    assert dofbot.accept_observation(expected, True)[0] is True
    assert gs['pending_dofbot'] is None
    assert gs['move_history'] == ['b2e2'] and len(ctl.serial_port.writes) == 1
    assert gs['ai_analysis']['physical_board_confirmed'] is True


def test_serial_hardware_start_does_not_home_or_need_joint_calibration(game):
    gs, ctl, client = game
    gs['is_game_running'] = False
    class Engine:
        is_ready = True
        def reset_game(self):
            pass
    with patch.object(web_app, 'get_ai_engine', return_value=Engine()), \
         patch.object(web_app, 'send_homing_command_to_controller', side_effect=AssertionError('homing')):
        data = client.post('/api/game/start', json={'mode': 'hardware', 'input_source': 'virtual'}).get_json()
    assert data['success'] and gs['is_game_running']
    assert not ctl.serial_port.writes


def test_mock_simulation_still_works_without_camera_or_serial(game):
    gs, _, _ = game
    web_app.config.ROBOT_BACKEND = 'mock'
    result = web_app.apply_ai_best_move('b2e2')
    assert result.success and gs['move_history'] == ['b2e2']
    assert gs['ai_analysis']['serial_payload'] is None
    assert gs['ai_analysis']['robot_send_success'] is None


def test_explicit_connect_uses_pyserial_and_sends_no_command(game):
    gs, ctl, _ = game
    ctl.close()
    with patch('serial.Serial', FakeSerial):
        ctl.connect('COM_TEST', 57600)
    assert ctl.status()['port'] == 'COM_TEST'
    assert ctl.status()['baud'] == 57600
    assert not ctl.serial_port.writes


def test_serial_cannot_execute_joint_steps_or_read_angles(game):
    gs, ctl, client = game
    with pytest.raises(ValueError):
        ctl.execute({'steps': [{'angles': [90]*6}], 'capture': False})
    with pytest.raises(ValueError):
        ctl.read_angles()
    gs['is_game_running'] = False
    assert client.post('/api/dofbot/park', json={}).status_code == 410
    assert not ctl.serial_port.writes


def test_old_web_tcp_motion_functions_are_disabled(game):
    with patch.object(web_app, 'get_robot_tcp_client', side_effect=AssertionError('TCP used')):
        assert not web_app.send_homing_command_to_controller().success
        assert not web_app.send_robot_command_to_controller([0, 0, 1, 1, 0]).success
        assert not web_app.probe_robot_controller().success


def test_export_and_passive_status_preserve_serial_result(game):
    gs, ctl, client = game
    web_app.apply_ai_best_move('b2e2')
    assert client.get('/api/game/export').get_json()['serial_payload'] == ctl.last_payload
    with patch('serial.Serial', side_effect=AssertionError('status opened serial')):
        assert client.get('/api/robot/status?probe=1').get_json()['backend'] == 'serial'


def test_no_local_servo_or_gpio_imports_remain_in_adapter():
    source = (Path(__file__).parents[1] / 'robot/dofbot_adapter.py').read_text(encoding='utf-8')
    for forbidden in ('from Arm_Lib', 'Arm_serial_servo_write', 'Arm_serial_servo_read',
                      'from gpiozero', 'import RPi', 'self.magnet.value'):
        assert forbidden not in source
