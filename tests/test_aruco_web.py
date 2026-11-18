"""ArUco-to-Web contracts, without opening a camera or issuing robot commands."""
import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from vision.recognizer import BoardRecognizer
from web_simulation import app as web_app
from web_simulation.routes import recognition


class ArucoWebTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "config.json"
        self.path.write_text(json.dumps({"test_only": True, "geometry_confirmed": False}))
        self.image = np.zeros((600, 800, 3), dtype=np.uint8)
        self.geometry = {"board_calibration_success": True, "field_calibration_success": False,
                         "board_pixel_corners": [[100, 60], [700, 60], [100, 540], [700, 540]]}
        self.layout = "\n".join(["........."] * 7 + ["....C...."] + ["........."] * 2)
        outer = self

        class Classifier:
            def pred(self, image, is_rgb):
                return None, None, [[0.95] * 9 for _ in range(10)], outer.layout

        with patch("vision.recognizer.ChessboardDetector", side_effect=AssertionError("pose must not load")):
            self.recognizer = BoardRecognizer(vision_backend="aruco", aruco_config_path=str(self.path),
                                              board_orientation="black-top", allow_approximate_geometry=True)
        self.recognizer.aruco._model = Classifier()
        self.original_state = copy.deepcopy(web_app.game_state)
        self.addCleanup(self.restore_state)
        self.client = web_app.app.test_client()

    def restore_state(self):
        web_app.game_state.clear()
        web_app.game_state.update(self.original_state)

    def test_uploaded_image_uses_aruco_without_camera_or_game_mutation(self):
        web_app.game_state["is_game_running"] = False
        before = copy.deepcopy(web_app.game_state)
        encoded = base64.b64encode(cv2.imencode(".png", self.image)[1]).decode()
        with patch.object(recognition, "image_recognizer", self.recognizer), \
                patch("vision.aruco.inspect_frame", return_value=(self.geometry, None, None)), \
                patch.object(web_app, "get_recognizer", side_effect=AssertionError("no camera")):
            response = self.client.post("/api/vision/observation", json={"image": encoded})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["preview_only"])
        self.assertEqual(payload["observed_board_state"], {"4,7": "C"})
        self.assertIn("unconfirmed_geometry", payload["observation"]["reasons"])
        self.assertEqual(web_app.game_state, before)

    def test_dynamic_aruco_preview_cannot_trigger_move_or_overwrite_active_game(self):
        web_app.game_state.update(is_game_running=True, vision_pause_until=0.0,
                                  robot_moving=False, ai_thinking=False,
                                  awaiting_physical_baseline=False)
        self.recognizer.dynamic_tracker.saved_board = {}
        before = copy.deepcopy(web_app.game_state)
        with patch.object(web_app, "get_recognizer", return_value=self.recognizer), \
                patch.object(self.recognizer.camera_manager, "capture_frame", return_value=self.image), \
                patch("vision.aruco.inspect_frame", return_value=(self.geometry, None, None)), \
                patch.object(web_app, "send_robot_command_to_controller") as sender:
            response = self.client.post("/api/recognize/dynamic", json={})
        payload = response.get_json()
        self.assertEqual(payload["event"], "vision_preview")
        self.assertFalse(payload["stable"])
        self.assertIsNone(payload["move"])
        sender.assert_not_called()
        self.assertEqual(web_app.game_state, before)

    def test_missing_marker_clears_previous_observation_and_skips_classification(self):
        with patch("vision.aruco.inspect_frame", return_value=(self.geometry, None, None)):
            self.assertEqual(self.recognizer.recognize_observation(self.image)["board_state"], {"4,7": "C"})
        with patch("vision.aruco.inspect_frame", return_value=({"board_calibration_success": False}, None, None)), \
                patch.object(self.recognizer.aruco._model, "pred", side_effect=AssertionError("must not classify")):
            invalid = self.recognizer.recognize_observation(self.image)
        self.assertEqual(invalid["status"], "rejected")
        self.assertIsNone(invalid["board_state"])
        self.assertEqual(invalid["consecutive_frames"], 0)

    def test_accepted_board_api_does_not_leak_raw_aruco_prediction(self):
        with patch("vision.aruco.inspect_frame", return_value=(self.geometry, None, None)):
            self.assertIsNone(self.recognizer.recognize_board(self.image))
        self.assertIsNotNone(self.recognizer.last_observation)

    def test_unknown_orientation_keeps_grid_observations_without_fake_web_coordinates(self):
        self.recognizer.aruco._orientation = "unknown"
        self.recognizer.aruco.reset()
        with patch("vision.aruco.inspect_frame", return_value=(self.geometry, None, None)):
            result = self.recognizer.recognize_observation(self.image)
        self.assertIsNone(result["board_state"])
        self.assertEqual(len(result["cells"]), 90)

    def test_vision_only_mode_blocks_game_actions(self):
        with patch.object(web_app.config, "VISION_ONLY", True, create=True):
            response = self.client.post("/api/game/start", json={})
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
