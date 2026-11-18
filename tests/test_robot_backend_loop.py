import unittest
import time
from unittest.mock import patch

from robot.protocol import RobotArmCommand, RobotSendResult
from web_simulation import app as web_app


# 已取消的 TCP 运动/归零用例由 test_serial_decision.py 的串口、去重和视觉同步测试替代。
class RobotBackendLoopTests(unittest.TestCase):
    def setUp(self):
        self.original_game_state = dict(web_app.game_state)
        web_app.game_state['is_game_running'] = False
        web_app.game_state['robot_mode'] = 'hardware'
        self.client = web_app.app.test_client()

    def tearDown(self):
        web_app.game_state.clear()
        web_app.game_state.update(self.original_game_state)

    def test_backend_uses_top_right_origin_for_ai_move(self):
        command = web_app.uci_to_robot_command("a0i9", board_state={})

        self.assertEqual(command, [272, 272, 0, 0, 0])

    def test_startup_ip_parameter_accepts_last_octet_or_full_ip(self):
        self.assertEqual(
            web_app.parse_robot_ip_parameter("103", "192.168.0.102"),
            "192.168.0.103",
        )
        self.assertEqual(
            web_app.parse_robot_ip_parameter("192.168.0.104", "192.168.0.102"),
            "192.168.0.104",
        )
        self.assertEqual(
            web_app.parse_robot_ip_parameter("", "192.168.0.102"),
            "192.168.0.102",
        )
        with self.assertRaises(ValueError):
            web_app.parse_robot_ip_parameter("300", "192.168.0.102")

    def test_startup_board_spacing_requires_positive_integer(self):
        self.assertEqual(web_app.parse_positive_int_parameter("35", 34, "横向"), 35)
        self.assertEqual(web_app.parse_positive_int_parameter("", 34, "横向"), 34)
        with self.assertRaises(ValueError):
            web_app.parse_positive_int_parameter("0", 34, "横向")
        with self.assertRaises(ValueError):
            web_app.parse_positive_int_parameter("34.5", 34, "横向")


    def test_start_game_simulation_mode_skips_homing(self):
        with (
            patch.object(web_app, "send_homing_command_to_controller") as homing,
            patch.object(web_app, "get_ai_engine") as get_engine,
        ):
            response = self.client.post("/api/game/start", json={"mode": "simulation"})

        data = response.get_json()
        homing.assert_not_called()
        get_engine.return_value.reset_game.assert_called_once_with()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["robot_mode"], "simulation")
        self.assertEqual(web_app.game_state["robot_mode"], "simulation")


    def test_dynamic_endpoint_locks_robot_physical_baseline_without_accepting_move(self):
        class FakeCameraManager:
            def capture_frame(self):
                return object()

        class FakeDynamicRecognizer:
            def __init__(self):
                self.camera_manager = FakeCameraManager()
                self.dynamic_tracker = type("Tracker", (), {"saved_board": None})()
                self.synced_boards = []
                self.reset_count = 0

            def sync_dynamic_baseline(self, board):
                self.synced_boards.append(dict(board))

            def reset_dynamic_tracking(self):
                self.reset_count += 1
                self.dynamic_tracker.saved_board = None

            def recognize_dynamic_frame(self, _frame):
                return {
                    "stable": True,
                    "event": "move",
                    "message": "容错识别: C (4, 7)->(4, 3)，忽略噪声点6个",
                    "move": {
                        "from": {"col": 4, "row": 7},
                        "to": {"col": 4, "row": 3},
                        "piece": "C",
                    },
                    "board_state": {(4, 3): "C"},
                }

        fake_recognizer = FakeDynamicRecognizer()
        web_app.game_state.update(
            is_game_running=True,
            robot_mode="hardware",
            board_state={"4,3": "C"},
            current_fen="9/9/9/9/9/9/4C4/9/9/9 w - - 0 1",
            move_history=["b6e6", "c7e6"],
            display_history=["b6e6", "204,60,136,90,1"],
            turn_count=2,
            robot_moving=False,
            vision_pause_reason="",
            vision_pause_until=0.0,
            awaiting_physical_baseline=True,
        )

        with patch.object(web_app, "get_recognizer", return_value=fake_recognizer):
            self.client.post("/api/recognize/dynamic", json={})
            self.client.post("/api/recognize/dynamic", json={})
            response = self.client.post("/api/recognize/dynamic", json={})

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["event"], "robot_board_confirmed")
        self.assertIsNone(data["move"])
        self.assertFalse(web_app.game_state["awaiting_physical_baseline"])
        self.assertEqual(fake_recognizer.synced_boards[-1], {(4, 3): "C"})
        self.assertEqual(web_app.game_state["move_history"], ["b6e6", "c7e6"])

    def test_dynamic_endpoint_requires_consecutive_robot_baseline_matches(self):
        class FakeCameraManager:
            def capture_frame(self):
                return object()

        class FakeDynamicRecognizer:
            def __init__(self):
                self.camera_manager = FakeCameraManager()
                self.dynamic_tracker = type("Tracker", (), {"saved_board": None})()
                self.synced_boards = []
                self.reset_count = 0

            def sync_dynamic_baseline(self, board):
                self.synced_boards.append(dict(board))

            def reset_dynamic_tracking(self):
                self.reset_count += 1
                self.dynamic_tracker.saved_board = None

            def recognize_dynamic_frame(self, _frame):
                return {
                    "stable": True,
                    "event": "initial_locked",
                    "message": "initial locked",
                    "move": None,
                    "board_state": {(4, 3): "C"},
                }

        fake_recognizer = FakeDynamicRecognizer()
        web_app.game_state.update(
            is_game_running=True,
            robot_mode="hardware",
            board_state={"4,3": "C"},
            current_fen="9/9/9/9/9/9/4C4/9/9/9 w - - 0 1",
            move_history=["b6e6", "c7e6"],
            display_history=["b6e6", "204,60,136,90,1"],
            turn_count=2,
            robot_moving=False,
            vision_pause_reason="",
            vision_pause_until=0.0,
            awaiting_physical_baseline=True,
            robot_baseline_match_count=0,
        )

        with patch.object(web_app, "get_recognizer", return_value=fake_recognizer):
            first = self.client.post("/api/recognize/dynamic", json={}).get_json()
            second = self.client.post("/api/recognize/dynamic", json={}).get_json()

        self.assertEqual(first["event"], "robot_board_waiting")
        self.assertEqual(second["event"], "robot_board_waiting")
        self.assertTrue(web_app.game_state["awaiting_physical_baseline"])
        self.assertEqual(fake_recognizer.synced_boards, [])
        self.assertEqual(web_app.game_state["move_history"], ["b6e6", "c7e6"])

    def test_dynamic_endpoint_keeps_waiting_when_robot_baseline_mismatches_expected_board(self):
        class FakeCameraManager:
            def capture_frame(self):
                return object()

        class FakeDynamicRecognizer:
            def __init__(self):
                self.camera_manager = FakeCameraManager()
                self.dynamic_tracker = type("Tracker", (), {"saved_board": None})()
                self.reset_count = 0
                self.synced_boards = []

            def sync_dynamic_baseline(self, board):
                self.synced_boards.append(dict(board))

            def reset_dynamic_tracking(self):
                self.reset_count += 1
                self.dynamic_tracker.saved_board = None

            def recognize_dynamic_frame(self, _frame):
                return {
                    "stable": True,
                    "event": "initial_locked",
                    "message": "初始基准已锁定",
                    "move": None,
                    "board_state": {(4, 3): "C", (1, 0): "r"},
                }

        fake_recognizer = FakeDynamicRecognizer()
        web_app.game_state.update(
            is_game_running=True,
            robot_mode="hardware",
            board_state={"4,3": "C", "0,0": "r"},
            current_fen="r8/9/9/9/9/9/4C4/9/9/9 w - - 0 1",
            move_history=["b6e6", "c7e6"],
            display_history=["b6e6", "204,60,136,90,1"],
            turn_count=2,
            robot_moving=False,
            vision_pause_reason="",
            vision_pause_until=0.0,
            awaiting_physical_baseline=True,
        )

        with patch.object(web_app, "get_recognizer", return_value=fake_recognizer):
            response = self.client.post("/api/recognize/dynamic", json={})

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["event"], "robot_board_waiting")
        self.assertIsNone(data["move"])
        self.assertTrue(web_app.game_state["awaiting_physical_baseline"])
        self.assertEqual(fake_recognizer.reset_count, 1)
        self.assertEqual(fake_recognizer.synced_boards, [])
        self.assertEqual(web_app.game_state["move_history"], ["b6e6", "c7e6"])

    def test_dynamic_endpoint_ignores_player_like_move_during_post_robot_settle_guard(self):
        class FakeCameraManager:
            def capture_frame(self):
                return object()

        class FakeDynamicRecognizer:
            def __init__(self):
                self.camera_manager = FakeCameraManager()
                self.dynamic_tracker = type("Tracker", (), {"saved_board": {}})()
                self.synced_boards = []

            def sync_dynamic_baseline(self, board):
                self.synced_boards.append(dict(board))

            def recognize_dynamic_frame(self, _frame):
                return {
                    "stable": True,
                    "event": "move",
                    "message": "N (1, 9)->(2, 7)",
                    "move": {
                        "from": {"col": 1, "row": 9},
                        "to": {"col": 2, "row": 7},
                        "piece": "N",
                    },
                    "board_state": {
                        (0, 0): "r",
                        (2, 7): "N",
                    },
                }

        fake_recognizer = FakeDynamicRecognizer()
        web_app.game_state.update(
            is_game_running=True,
            robot_mode="hardware",
            board_state={"0,0": "r", "1,9": "N"},
            current_fen="r8/9/9/9/9/9/9/9/9/1N7 w - - 0 1",
            move_history=["g4g7", "b7g7"],
            display_history=["g4g7", "238,60,68,60,1"],
            turn_count=2,
            robot_moving=False,
            vision_pause_reason="",
            vision_pause_until=0.0,
            awaiting_physical_baseline=False,
            post_robot_guard_until=time.monotonic() + 10.0,
        )

        with patch.object(web_app, "get_recognizer", return_value=fake_recognizer):
            response = self.client.post("/api/recognize/dynamic", json={})

        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["event"], "robot_settling")
        self.assertIsNone(data["move"])
        self.assertEqual(web_app.game_state["move_history"], ["g4g7", "b7g7"])
        self.assertEqual(web_app.game_state["turn_count"], 2)
        self.assertEqual(fake_recognizer.synced_boards[-1], {(0, 0): "r", (1, 9): "N"})


    def test_repeated_last_player_move_after_ai_is_duplicate(self):
        web_app.game_state.update(
            move_history=["e6g7", "c7e6"],
            last_player_move="e6g7",
        )

        self.assertTrue(web_app.is_duplicate_player_move("e6g7"))
        self.assertFalse(web_app.is_duplicate_player_move("a0a1"))


if __name__ == "__main__":
    unittest.main()
