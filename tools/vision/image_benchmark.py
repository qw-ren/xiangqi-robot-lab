"""Offline CH-RO CPU inference; no robot or game entry points are imported."""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

os.environ['CHRO_ONNX_PROVIDERS'] = 'CPUExecutionProvider'
os.environ['CHRO_ONNX_INTRA_THREADS'] = '2'
os.environ['CHRO_ONNX_INTER_THREADS'] = '1'
ROOT = Path(os.environ.get('CHRO_ROOT', Path(__file__).resolve().parents[2]))
DEPS = os.environ.get('CHRO_VISION_DEPS')
if DEPS:
    sys.path.insert(0, DEPS)
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from core.chessboard_detector import ChessboardDetector

parser = argparse.ArgumentParser()
parser.add_argument('--image', required=True)
parser.add_argument('--out', required=True)
parser.add_argument('--repeats', type=int, default=5)
parser.add_argument('--ground-truth')
args = parser.parse_args()
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
root = ROOT
start = time.perf_counter()
det = ChessboardDetector(str(root/'model/pose/4_v6-0301.onnx'), str(root/'model/layout_recognition/nano_v3-0319.onnx'))
report = {'model_load_ms': (time.perf_counter()-start)*1000,
          'image': args.image, 'providers': [det.pose.session.get_providers(), det.full_classifier.session.get_providers()],
          'opencv': cv2.__version__, 'intra_threads': 2, 'inter_threads': 1,
          'timing_scope': 'RGB input -> RTMPose -> perspective transform -> whole-board classifier; excludes file IO/RGB conversion/resizing/debug drawing',
          'runs': []}
bgr = cv2.imread(args.image)
if bgr is None:
    raise ValueError('Cannot read image')
rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
report['original_shape'] = list(rgb.shape)
if rgb.shape[1] > 960:
    rgb = cv2.resize(rgb, (960, int(rgb.shape[0]*960/rgb.shape[1])), interpolation=cv2.INTER_AREA)
report['input_shape'] = list(rgb.shape)
first_layout = None
for phase, count, force in [('first', 1, True), ('warm_full', args.repeats, True), ('warm_cached', args.repeats, False)]:
    for i in range(count):
        start = time.perf_counter()
        _, warped, layout, scores, info = det.pred_detect_board_and_classifier(rgb, force_pose=force)
        ms = (time.perf_counter()-start)*1000
        report['runs'].append({'phase': phase, 'index': i, 'ms': ms, 'layout': layout, 'time_info': info,
                               'used_pose_cache': 'pose=cached' in info, 'returned_layout': layout is not None})
        print(phase, i, round(ms, 3), info, flush=True)
        if phase == 'first':
            first_layout = layout
            report['first_scores'] = None if scores is None else np.asarray(scores).tolist()
            report['first_keypoints'] = None if det.cached_keypoints is None else det.cached_keypoints.tolist()
            report['first_pose_scores'] = None if det.cached_scores is None else det.cached_scores.tolist()
            if warped is not None:
                cv2.imwrite(str(out/'warped.png'), cv2.cvtColor(warped, cv2.COLOR_RGB2BGR))
            if det.cached_keypoints is not None:
                # Annotate the full-resolution original, not only the resized inference input.
                pts = det.cached_keypoints * np.array([bgr.shape[1]/rgb.shape[1], bgr.shape[0]/rgb.shape[0]])
                debug = bgr.copy()
                for label, pt in zip(det.pose.bone_names, pts):
                    xy = tuple(np.rint(pt).astype(int))
                    cv2.circle(debug, xy, 9, (0, 0, 255), 3)
                    cv2.putText(debug, label, xy, cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 0, 255), 2)
                cv2.imwrite(str(out/'original_corners.png'), debug)
for phase in ['warm_full', 'warm_cached']:
    values = [r['ms'] for r in report['runs'] if r['phase'] == phase]
    if values:
        report[phase] = {'mean_ms': float(np.mean(values)), 'median_ms': float(np.median(values)),
                         'min_ms': min(values), 'max_ms': max(values)}
report['layout_consistent'] = len(set(r['layout'] for r in report['runs'])) == 1
if first_layout is not None:
    (out/'layout.txt').write_text(first_layout+'\n')
    rows = first_layout.splitlines()
    truth = Path(args.ground_truth).read_text().strip().splitlines() if args.ground_truth else None
    if len(rows) != 10 or any(len(row) != 9 for row in rows):
        raise ValueError('Unexpected layout shape')
    cells = []
    for r in range(10):
        for c in range(9):
            p = rows[r][c]
            expected = truth[r][c] if truth else ''
            cells.append({'row_0based': r, 'col_0based': c, 'piece': p,
                          'color': 'red' if p.isupper() else 'black' if p.islower() and p != 'x' else 'other' if p == 'x' else 'empty',
                          'confidence': report['first_scores'][r][c], 'expected': expected,
                          'matches': p == expected if truth else ''})
    with (out/'cells.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(cells[0])); writer.writeheader(); writer.writerows(cells)
    if truth:
        report['manual_reference'] = args.ground_truth
        report['wrong_cells'] = [c for c in cells if not c['matches']]
        report['matching_cells'] = sum(c['matches'] for c in cells)
        report['occupied_reference_cells'] = sum(c['expected'] not in '.x' for c in cells)
(out/'result.json').write_text(json.dumps(report, indent=2))
print(json.dumps({k: v for k, v in report.items() if k in ['model_load_ms', 'warm_full', 'warm_cached', 'wrong_cells', 'matching_cells', 'layout_consistent']}, indent=2))
