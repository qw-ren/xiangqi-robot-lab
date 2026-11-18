"""Independent image-only ArUco experiment; leaves RTMPose path unchanged.

Configuration marker corners use marker-local TL, TR, BR, BL order (decoded
orientation), not sorting corners by screen coordinates. board_xy is measured
in grid intervals: upper-left intersection=(0,0), lower-right=(8,9).
"""
import argparse
import os
import json
import sys
from pathlib import Path

ROOT = Path(os.environ.get('CHRO_ROOT', Path(__file__).resolve().parents[2]))
DEPS = os.environ.get('CHRO_VISION_DEPS')
if DEPS:
    sys.path.insert(0, DEPS)
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from vision.aruco import inspect_frame


def inspect_image(image_path: Path, config: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('Image cannot be read: ' + str(image_path))
    result, debug, warped = inspect_frame(image, config)
    result['image'] = str(image_path)
    for name, value in [('markers.png', debug), ('board_warped.png', warped)]:
        if value is not None:
            cv2.imencode('.png', value)[1].tofile(out / name)
    (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def self_test(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    # TEST ONLY: mathematical grid and marker placement, unrelated to site hardware.
    image = np.full((1100, 1000, 3), 255, np.uint8)
    marker_positions = {10: (40, 40), 11: (850, 40), 12: (40, 950), 13: (850, 950)}
    config = {'test_only': True, 'dictionary': 'DICT_4X4_50', 'required_ids': [10, 11, 12, 13],
              'geometry_confirmed': True, 'geometry_source': 'TEST ONLY synthetic drawing: grid origin=(180,180)px, spacing=80px',
              'marker_corners_board_xy': {}}
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for marker_id, (x, y) in marker_positions.items():
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 100)
        # Rotate ID 11 to exercise decoded corner ordering, not screen sorting.
        if marker_id == 11:
            marker = cv2.rotate(marker, cv2.ROTATE_90_CLOCKWISE)
        image[y:y+100, x:x+100] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        points = [[x, y], [x+99, y], [x+99, y+99], [x, y+99]]
        if marker_id == 11:
            points = [points[1], points[2], points[3], points[0]]
        config['marker_corners_board_xy'][str(marker_id)] = [[(px-180)/80, (py-180)/80] for px, py in points]
    for c in range(9):
        cv2.line(image, (180+c*80, 180), (180+c*80, 900), (100, 100, 100), 1)
    for r in range(10):
        cv2.line(image, (180, 180+r*80), (820, 180+r*80), (100, 100, 100), 1)
    source = np.float32([[0, 0], [999, 0], [0, 1099], [999, 1099]])
    target = np.float32([[90, 50], [1010, 110], [40, 1120], [1080, 1150]])
    matrix = cv2.getPerspectiveTransform(source, target)
    distorted = cv2.warpPerspective(image, matrix, (1150, 1220), borderValue=(255, 255, 255))
    input_path = out/'synthetic_perspective.png'
    cv2.imwrite(str(input_path), distorted)
    (out/'TEST_ONLY_config.json').write_text(json.dumps(config, indent=2))
    complete = inspect_image(input_path, config, out/'complete')
    expected = cv2.perspectiveTransform(np.float32([[[180, 180], [820, 180], [180, 900], [820, 900]]]), matrix)[0]
    error = float(np.linalg.norm(np.array(complete.get('board_pixel_corners', [[1e6, 1e6]]*4))-expected, axis=1).max())
    assert complete['board_calibration_success'] and not complete['field_calibration_success'] and error<3, complete
    no_geometry = dict(config, geometry_confirmed=False, marker_corners_board_xy={})
    (out/'TEST_ONLY_detection_only_config.json').write_text(json.dumps(no_geometry, indent=2))
    detection = inspect_image(input_path, no_geometry, out/'detection_only')
    assert detection['all_required_markers_detected'] and not detection['board_calibration_success']
    missing_image = image.copy(); missing_image[950:1050, 850:950] = 255
    missing_path = out/'synthetic_missing_id13.png'
    cv2.imwrite(str(missing_path), cv2.warpPerspective(missing_image, matrix, (1150, 1220), borderValue=(255, 255, 255)))
    missing = inspect_image(missing_path, config, out/'missing_marker')
    assert missing['missing_ids'] == [13] and not missing['board_calibration_success']
    wrong_geometry = json.loads(json.dumps(config))
    wrong_geometry['marker_corners_board_xy']['11'] = wrong_geometry['marker_corners_board_xy']['11'][::-1]
    wrong = inspect_image(input_path, wrong_geometry, out/'wrong_corner_order')
    assert not wrong['board_calibration_success']
    summary = {'test_only': True, 'passed': True, 'cases': 4, 'rotated_marker_id': 11,
               'max_board_corner_error_px': error, 'complete_status': complete['status'],
               'detection_only_status': detection['status'], 'missing_marker_status': missing['status'],
               'wrong_corner_order_status': wrong['status'], 'field_calibration_performed': False}
    (out/'selftest.json').write_text(json.dumps(summary, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        result = self_test(args.out)
    else:
        if not args.image or not args.config:
            parser.error('--image and --config are required without --self-test')
        result = inspect_image(args.image, json.loads(args.config.read_text()), args.out)
    print(json.dumps(result, indent=2))
