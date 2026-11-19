"""Shared ArUco geometry and image recognition, without robot commands."""
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from core.keypoint_helper import extract_chessboard
from .observation import ObservationStream


def classify_board(image: np.ndarray, corners: np.ndarray, classifier):
    """Use the validated 960 px preview warp path for both Web and CLI."""
    preview = cv2.resize(image, (960, round(image.shape[0] * 960 / image.shape[1])),
                         interpolation=cv2.INTER_AREA)
    scaled = np.float32(corners) * np.float32([
        preview.shape[1] / image.shape[1], preview.shape[0] / image.shape[0]])
    warped, _, _ = extract_chessboard(preview, scaled)
    _, _, scores, layout = classifier.pred(warped, is_rgb=False)
    return warped, scores, layout


class ArucoRecognizer:
    """One model/session and a serialized stream of fresh image observations."""

    def __init__(self, config_path, model_path, orientation="unknown", allow_approximate=False,
                 stable_frames=3, min_score=0.8):
        self.config_path = Path(config_path)
        raw = self.config_path.read_bytes()
        self.config = json.loads(raw.decode("utf-8-sig"))
        if allow_approximate:
            if self.config.get("test_only") is not True:
                raise ValueError("approximate geometry requires test_only=true")
            self.config = dict(self.config, geometry_confirmed=True)
        self.allow_approximate = allow_approximate
        self.config_sha256 = hashlib.sha256(raw).hexdigest()
        self.model_path = str(model_path)
        self._model = None
        self._lock = threading.RLock()
        self._orientation = orientation
        self._stable_frames = stable_frames
        self._min_score = min_score
        self.frame_id = 0
        self.reset()

    def reset(self):
        with self._lock:
            self.stream = ObservationStream(self._orientation, self._stable_frames, self._min_score)

    def observe(self, image: np.ndarray, fallback=None) -> dict:
        with self._lock:
            self.frame_id += 1
            captured_at = datetime.now().astimezone().isoformat()
            calibration = {}
            layout = scores = None
            source = None
            if image is not None:
                calibration, _, _ = inspect_frame(image, self.config)
                calibration["frame_id"] = self.frame_id
                calibration["approximate_geometry_override"] = self.allow_approximate
                if calibration["board_calibration_success"]:
                    if self._model is None:
                        from core.runonnx.full_classifier import FULL_CLASSIFIER_ONNX
                        self._model = FULL_CLASSIFIER_ONNX(self.model_path)
                    _, scores, layout = classify_board(image, calibration["board_pixel_corners"], self._model)
                    source = "aruco"
                elif fallback is not None:
                    layout, scores = fallback(image)
                    if layout is not None:
                        source = "rtmpose_fallback"
            observation = self.stream.observe(frame_id=self.frame_id, captured_at=captured_at,
                                               calibration=calibration, layout=layout, scores=scores,
                                               source=source)
            observation["config_sha256"] = self.config_sha256
            return observation


def inspect_frame(image: np.ndarray, config: dict) -> tuple:
    """Shared in-memory geometry validation; returns metadata, debug and warp."""
    warped = None
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, config['dictionary']))
    params = aruco.DetectorParameters()
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    detector = aruco.ArucoDetector(dictionary, params)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, ids, rejected = detector.detectMarkers(gray)
    found = [] if ids is None else ids.flatten().tolist()
    expected = config['required_ids']
    preprocessing = 'grayscale'
    if not set(expected).issubset(found):
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        retry_corners, retry_ids, retry_rejected = detector.detectMarkers(enhanced)
        retry_found = [] if retry_ids is None else retry_ids.flatten().tolist()
        if len(set(expected).intersection(retry_found)) > len(set(expected).intersection(found)):
            corners, ids, rejected, found = retry_corners, retry_ids, retry_rejected, retry_found
            preprocessing = 'clahe'
    result = {'dictionary': config['dictionary'],
              'marker_preprocessing': preprocessing,
              'frame_size': [image.shape[1], image.shape[0]],
              'test_only': bool(config.get('test_only', True)), 'detected_ids': found,
              'missing_ids': sorted(set(expected)-set(found)), 'unexpected_ids': sorted(set(found)-set(expected)),
              'duplicate_ids': sorted({i for i in found if found.count(i)>1}),
              'marker_detection_success': bool(found), 'all_required_markers_detected': set(expected).issubset(found),
              'board_calibration_success': False, 'field_calibration_success': False,
              'geometry_confirmed': bool(config.get('geometry_confirmed', False)),
              'geometry_source': config.get('geometry_source', 'unspecified'),
              'rejected_candidates': len(rejected),
              'corner_order': 'marker-local TL,TR,BR,BL in decoded orientation',
              'markers': [{'id': int(i), 'pixel_corners': c.reshape(4, 2).tolist()} for i, c in zip(found, corners)]}
    debug = image.copy()
    if found:
        aruco.drawDetectedMarkers(debug, corners, ids)
    geometry = config.get('marker_corners_board_xy', {})
    result['missing_geometry_ids'] = [i for i in expected if str(i) not in geometry]
    reason = None
    if result['missing_ids']:
        reason = 'required_markers_missing'
    elif result['duplicate_ids']:
        reason = 'duplicate_marker_ids'
    elif result['missing_geometry_ids'] or not result['geometry_confirmed']:
        reason = 'marker_to_board_geometry_missing_or_unconfirmed'
    else:
        by_id = {i: c.reshape(4, 2) for i, c in zip(found, corners)}
        board_points = np.float32([pt for i in expected for pt in geometry[str(i)]])
        pixel_points = np.float32([pt for i in expected for pt in by_id[i]])
        if board_points.shape != (len(expected)*4, 2):
            raise ValueError('Each marker requires four board_xy corners')
        homography, mask = cv2.findHomography(board_points, pixel_points, cv2.RANSAC, 3.0)
        if homography is None or not np.isfinite(homography).all():
            reason = 'degenerate_geometry'
        else:
            projected = cv2.perspectiveTransform(board_points.reshape(-1, 1, 2), homography).reshape(-1, 2)
            errors = np.linalg.norm(projected-pixel_points, axis=1)
            # All configured marker corners must agree, including RANSAC outliers.
            result['max_reprojection_error_px'] = float(errors.max())
            board_corners = cv2.perspectiveTransform(np.float32([[[0, 0], [8, 0], [0, 9], [8, 9]]]), homography)[0]
            result['board_corner_order'] = ['A0/TL', 'A8/TR', 'J0/BL', 'J8/BR']
            result['board_pixel_corners'] = board_corners.tolist()
            h, w = image.shape[:2]
            inside = bool(np.all(board_corners[:, 0]>=0) and np.all(board_corners[:, 0]<w) and np.all(board_corners[:, 1]>=0) and np.all(board_corners[:, 1]<h))
            polygon = board_corners[[0, 1, 3, 2]].astype(np.float32)
            valid_shape = bool(cv2.isContourConvex(polygon) and abs(cv2.contourArea(polygon))>100)
            if errors.max()>config.get('max_reprojection_error_px', 3.0):
                reason = 'inconsistent_marker_geometry'
            elif not inside or not valid_shape:
                reason = 'board_outside_image_or_degenerate'
            else:
                warped, _, _ = extract_chessboard(image, board_corners)
                for label, point in zip(result['board_corner_order'], board_corners):
                    xy = tuple(np.rint(point).astype(int))
                    cv2.circle(debug, xy, 7, (0, 0, 255), 2)
                    cv2.putText(debug, label, xy, cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 255), 1)
                result['board_calibration_success'] = True
                result['field_calibration_success'] = not result['test_only']
    result['status'] = reason or ('test_board_calibration_success' if result['test_only'] else 'board_calibration_success')
    return result, debug, warped
