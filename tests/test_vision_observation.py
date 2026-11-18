"""Exercise coordinate and rejection contracts without camera/model dependencies."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.vision.observation import ObservationStream, write_latest


class ObservationContractTests(unittest.TestCase):
    def setUp(self):
        self.rows = ["........."] * 10
        self.rows[7] = "....C...."
        self.layout = "\n".join(self.rows)
        self.scores = [[0.95] * 9 for _ in range(10)]
        self.calibration = {"board_calibration_success": True, "field_calibration_success": True}

    def observe(self, stream, layout=None, calibration=None, scores=None):
        return stream.observe(frame_id=1, captured_at="2026-11-18T12:00:00+08:00",
                              calibration=self.calibration if calibration is None else calibration,
                              layout=self.layout if layout is None else layout,
                              scores=self.scores if scores is None else scores)

    def test_web_coordinates_and_piece_case_survive_conversion(self):
        result = self.observe(ObservationStream("black-top"))
        self.assertEqual(result["board_state"], {"4,7": "C"})
        self.assertEqual(len(result["cells"]), 90)
        result = self.observe(ObservationStream("red-top"))
        self.assertEqual(result["board_state"], {"4,2": "C"})
        self.assertEqual(result["cells"][67]["row"], 7)

    def test_orientation_is_never_inferred_from_piece_predictions(self):
        result = self.observe(ObservationStream())
        self.assertIsNone(result["board_state"])
        self.assertIn("orientation_unknown", result["reasons"])

    def test_persistent_high_score_error_still_requires_review(self):
        stream = ObservationStream("black-top")
        wrong = self.layout.replace("C", "B")
        for _ in range(3):
            result = self.observe(stream, layout=wrong)
        self.assertTrue(result["stable"])
        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["can_update_game"])

    def test_unknown_is_not_turned_into_an_empty_square(self):
        result = self.observe(ObservationStream("black-top"), layout=self.layout.replace("C", "x"))
        self.assertEqual(result["status"], "rejected")
        self.assertIn("unknown_cells", result["reasons"])
        self.assertEqual(result["cells"][67]["piece"], "x")

    def test_bad_frames_clear_stability_even_when_layout_is_unchanged(self):
        stream = ObservationStream("black-top", stable_frames=2)
        self.observe(stream)
        self.assertTrue(self.observe(stream)["stable"])
        low = [list(row) for row in self.scores]
        low[0][0] = 0.1  # Empty cells also participate in quality checks.
        self.assertFalse(self.observe(stream, scores=low)["stable"])
        self.assertFalse(self.observe(stream)["stable"])
        self.assertFalse(self.observe(stream, calibration={})["stable"])
        self.assertFalse(self.observe(stream)["stable"])

    def test_test_geometry_cannot_become_a_reviewable_field_board(self):
        stream = ObservationStream("black-top", stable_frames=1)
        result = self.observe(stream, calibration={"board_calibration_success": True})
        self.assertEqual(result["status"], "provisional")
        self.assertIn("unconfirmed_geometry", result["reasons"])

    def test_shape_symbol_and_nonfinite_scores_are_rejected(self):
        for layout in ("bad", self.layout.replace("C", "?")):
            with self.assertRaises(ValueError):
                self.observe(ObservationStream(), layout=layout)
        for value in (float("nan"), float("inf"), -0.1, 1.1):
            scores = [list(row) for row in self.scores]
            scores[0][0] = value
            with self.assertRaises(ValueError):
                self.observe(ObservationStream(), scores=scores)

    def test_no_prediction_replaces_previous_board_and_atomic_file(self):
        stream = ObservationStream("black-top", stable_frames=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "latest.json"
            write_latest(path, self.observe(stream))
            invalid = stream.observe(frame_id=2, captured_at="now", calibration={})
            write_latest(path, invalid)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "rejected")
            self.assertIsNone(saved["board_state"])
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
