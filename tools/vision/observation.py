"""JSON boundary for raw board observations; no camera or control dependencies."""
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Optional


PIECES = frozenset("KABNRCPkabnrcp")


class ObservationStream:
    """Track consecutive predictions, without treating stability as correctness."""

    def __init__(self, orientation: str = "unknown", stable_frames: int = 3,
                 min_score: float = 0.8):
        if orientation not in ("unknown", "black-top", "red-top"):
            raise ValueError("orientation must be unknown, black-top or red-top")
        if stable_frames < 1 or not math.isfinite(min_score) or not 0 <= min_score <= 1:
            raise ValueError("stable_frames >= 1 and 0 <= min_score <= 1 required")
        self.orientation = orientation
        self.stable_frames = stable_frames
        self.min_score = min_score
        self._previous = None
        self._count = 0

    def observe(self, *, frame_id: int, captured_at: str, calibration: dict,
                layout: Optional[str] = None, scores=None) -> dict:
        """Return a complete snapshot. Empty/failed input clears temporal history.

        Cell row/col always refer to the input image. board_state uses the Web
        convention (black home side at row 0), only after explicit orientation.
        """
        reasons = []
        localized = bool(calibration.get("board_calibration_success"))
        if not localized:
            reasons.append("localization_failed")
        if not calibration.get("field_calibration_success"):
            reasons.append("unconfirmed_geometry")
        if self.orientation == "unknown":
            reasons.append("orientation_unknown")
        cells = []
        board = {} if self.orientation != "unknown" else None
        if layout is not None:
            rows = layout.splitlines()
            if len(rows) != 10 or any(len(row) != 9 for row in rows):
                raise ValueError("layout must contain 10 rows of 9 symbols")
            if scores is None or len(scores) != 10 or any(len(row) != 9 for row in scores):
                raise ValueError("scores must have shape 10x9")
            for row, line in enumerate(rows):
                for col, piece in enumerate(line):
                    if piece not in PIECES and piece not in (".", "x"):
                        raise ValueError("invalid piece symbol: " + piece)
                    score = float(scores[row][col])
                    if not math.isfinite(score) or not 0 <= score <= 1:
                        raise ValueError("scores must be finite numbers in [0,1]")
                    cells.append({"row": row, "col": col, "piece": piece, "score": score})
                    if board is not None and piece in PIECES:
                        c, r = (8-col, 9-row) if self.orientation == "red-top" else (col, row)
                        board[f"{c},{r}"] = piece
            if any(cell["piece"] == "x" for cell in cells):
                reasons.append("unknown_cells")
            if any(cell["score"] < self.min_score for cell in cells):
                reasons.append("low_scores")
        else:
            reasons.append("no_prediction")
            board = None

        # Count only consecutive frames passing the basic prediction checks.
        prediction_ok = localized and layout is not None and not any(
            reason in reasons for reason in ("unknown_cells", "low_scores"))
        self._count = (self._count + 1 if layout == self._previous else 1) if prediction_ok else 0
        self._previous = layout if prediction_ok else None
        stable = self._count >= self.stable_frames
        if prediction_ok and not stable:
            reasons.append("warming_up")
        status = "review_required" if not reasons else "provisional"
        if not prediction_ok:
            status = "rejected"
        return {
            "schema_version": "1.0", "frame_id": frame_id, "captured_at": captured_at,
            "status": status, "reasons": reasons,
            "orientation": self.orientation,
            "coordinates": {"cells": "image_top_left_zero_based_row_col",
                            "board_state": "black_top_zero_based_col_row"},
            "layout": layout, "cells": cells, "board_state": board,
            "stable": stable, "consecutive_frames": self._count,
            "quality": {"min_score": self.min_score, "required_frames": self.stable_frames,
                        "occlusion_checked": False, "legal_transition_checked": False},
            "can_update_game": False,
            "calibration": dict(calibration),
        }


def write_latest(path: Path, observation: dict) -> None:
    """Atomically replace one complete JSON snapshot for another process to read."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(observation, ensure_ascii=False, allow_nan=False, indent=2)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
