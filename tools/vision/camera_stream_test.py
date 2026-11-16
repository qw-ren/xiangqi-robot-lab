"""Bounded USB capture test. Run under shell timeout in case a driver read blocks."""
import argparse
import json
import time
from pathlib import Path
import cv2

p = argparse.ArgumentParser()
p.add_argument('--device', default='/dev/video0')
p.add_argument('--seconds', type=float, default=10)
p.add_argument('--out', required=True)
a = p.parse_args()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
cap = cv2.VideoCapture(a.device, cv2.CAP_V4L2)
if not cap.isOpened():
    raise RuntimeError('Camera open failed')
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
cap.set(cv2.CAP_PROP_FPS, 10)
report = {'device': a.device, 'resolved_device': str(Path(a.device).resolve()),
          'actual_width': cap.get(cv2.CAP_PROP_FRAME_WIDTH), 'actual_height': cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
          'driver_reported_fps': cap.get(cv2.CAP_PROP_FPS), 'requested_seconds': a.seconds,
          'warmup_attempts': 10, 'warmup_failures': 0, 'successful_frames': 0, 'failed_reads': 0,
          'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
last = None
try:
    for _ in range(10):
        ok, frame = cap.read()
        report['warmup_failures'] += int(not ok)
    start = time.perf_counter()
    while time.perf_counter()-start < a.seconds:
        ok, frame = cap.read()
        if ok:
            report['successful_frames'] += 1; last = frame
        else:
            report['failed_reads'] += 1; time.sleep(.01)
    report['elapsed_seconds'] = time.perf_counter()-start
finally:
    cap.release()
report['measured_fps'] = report['successful_frames']/report['elapsed_seconds']
report['fps_definition'] = 'successful cap.read frames / elapsed wall time after 10 warmup attempts; not independent sensor exposure rate'
if last is not None:
    cv2.imwrite(str(out/'cropped_scene.png'), last)
    report['saved_frame_shape'] = list(last.shape)
(out/'capture.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
