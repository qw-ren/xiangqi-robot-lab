"""Fixed-camera Xiangqi vision preview with offline image replay."""
import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("CHRO_ROOT", Path(__file__).resolve().parents[2]))
if os.environ.get("CHRO_VISION_DEPS"):
    sys.path.insert(0, os.environ["CHRO_VISION_DEPS"])
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CHRO_ONNX_PROVIDERS", "CPUExecutionProvider")
os.environ.setdefault("CHRO_ONNX_INTRA_THREADS", "2")

import cv2
import numpy as np
from tools.vision.aruco_image_test import inspect_frame
from core.keypoint_helper import extract_chessboard
from core.runonnx.full_classifier import FULL_CLASSIFIER_ONNX
from tools.vision.observation import ObservationStream, write_latest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--camera", type=int)
    source.add_argument("--image", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference", type=Path, help="Optional 10x9 layout.txt for cell comparison")
    parser.add_argument("--allow-approximate-geometry", action="store_true", help="Use unconfirmed geometry only with test_only=true")
    parser.add_argument("--frames", type=int, default=0, help="Stop after N frames; 0 is unlimited")
    parser.add_argument("--no-preview", action="store_true", help="Disable GUI for capture smoke tests")
    parser.add_argument("--save-every", type=int, default=0, help="Save each Nth recognized frame; 0 disables")
    parser.add_argument("--latest-json", type=Path, help="Atomically publish every observation for Web integration")
    parser.add_argument("--board-orientation", choices=("unknown", "black-top", "red-top"), default="unknown",
                        help="Which home side is at the image top; must be explicitly confirmed")
    parser.add_argument("--stable-frames", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.8)
    args = parser.parse_args()
    if args.frames < 0 or args.save_every < 0:
        parser.error("frame counts must be nonnegative")
    try:
        observations = ObservationStream(args.board_orientation, args.stable_frames, args.min_score)
    except ValueError as error:
        parser.error(str(error))
    if args.latest_json:
        starting = observations.observe(frame_id=0, captured_at=datetime.now().astimezone().isoformat(),
                                        calibration={})
        starting["reasons"].append("source_starting")
        write_latest(args.latest_json, starting)

    config_bytes = args.config.read_bytes()
    config = json.loads(config_bytes.decode("utf-8-sig"))
    reference = None
    if args.reference:
        reference = args.reference.read_text(encoding="utf-8").strip().splitlines()
        if len(reference) != 10 or any(len(row) != 9 for row in reference):
            parser.error("reference must contain ten rows of nine piece symbols")
    if args.allow_approximate_geometry:
        if config.get("test_only") is not True:
            parser.error("approximate geometry requires test_only=true")
        config = dict(config, geometry_confirmed=True)
    model_path = ROOT / "model/layout_recognition/nano_v3-0319.onnx"
    model = FULL_CLASSIFIER_ONNX(str(model_path))
    model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    corners = None
    previous = None
    capture = None
    if args.camera is not None:
        capture = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        if not capture.isOpened():
            capture.release()
            parser.exit(1, f"Cannot open camera {args.camera}. Close other camera apps or try another index.\n")
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
        print(f"Camera {args.camera}: {capture.getBackendName()} "
              f"{capture.get(cv2.CAP_PROP_FRAME_WIDTH):g}x{capture.get(cv2.CAP_PROP_FRAME_HEIGHT):g}", flush=True)

    frame_id = 0
    recognized_frames = 0
    last_status = None
    calibration = None
    captured_at = datetime.now().astimezone().isoformat()
    try:
        while True:
            if capture is None:
                frame = cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR)
            else:
                ok, frame = capture.read()
                if not ok:
                    frame = None
            if frame is None:
                parser.exit(1, "Could not read the image or a camera frame.\n")

            frame_id += 1
            captured_at = datetime.now().astimezone().isoformat()
            status = "S save | R recalibrate | Q quit"
            if corners is None or args.latest_json:
                calibration, _, _ = inspect_frame(frame, config)
                status = calibration["status"]
                if status != last_status:
                    print(f"Localization: {status}; detected={calibration['detected_ids']}", flush=True)
                    last_status = status
                if calibration["board_calibration_success"]:
                    corners = np.float32(calibration["board_pixel_corners"])
                    calibration["approximate_geometry_override"] = args.allow_approximate_geometry
                    calibration["frame_id"] = frame_id
                    print(json.dumps(calibration), flush=True)
                else:
                    corners = None
            preview = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])),
                                 interpolation=cv2.INTER_AREA)
            warped = None
            layout = scores = None
            if corners is not None:
                scaled = corners * np.float32([preview.shape[1]/frame.shape[1], preview.shape[0]/frame.shape[0]])
                warped, _, _ = extract_chessboard(preview, scaled)
                _, _, scores, layout = model.pred(warped, is_rgb=False)
                recognized_frames += 1
                if layout != previous:
                    print(layout, flush=True)
                    previous = layout
                cv2.polylines(preview, [np.rint(scaled[[0, 1, 3, 2]]).astype(np.int32)], True, (0, 255, 0), 2)
            observation = observations.observe(frame_id=frame_id, captured_at=captured_at,
                                               calibration=calibration or {}, layout=layout, scores=scores)
            if args.latest_json:
                write_latest(args.latest_json, observation)
            if corners is None and capture is None:
                parser.exit(1, status + "\n")

            if capture is None:
                key = ord("s")
            elif args.no_preview:
                key = -1
            else:
                cv2.putText(preview, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 255, 0), 2)
                cv2.imshow("Xiangqi camera", preview)
                if warped is not None:
                    annotated = warped.copy()
                    for row, line in enumerate(layout.splitlines()):
                        for col, piece in enumerate(line):
                            if piece != ".":
                                x = round(50 + col * 350 / 8)
                                y = round(50 + row * 400 / 9)
                                cv2.putText(annotated, piece, (x-7, y+6), cv2.FONT_HERSHEY_SIMPLEX,
                                            .65, (255, 100, 0), 2)
                    cv2.imshow("Xiangqi recognition", annotated)
                key = cv2.waitKey(1) & 0xff
            if (key == ord("s") or (args.save_every and frame_id % args.save_every == 0)) and warped is not None:
                folder = args.out / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                folder.mkdir(parents=True)
                cv2.imencode(".png", frame)[1].tofile(folder / "frame.png")
                cv2.imencode(".png", warped)[1].tofile(folder / "warped.png")
                (folder / "result.json").write_text(json.dumps({
                    "frame_id": frame_id, "captured_at": captured_at,
                    "frame_shape": list(frame.shape), "warped_shape": list(warped.shape),
                    "source": str(args.image) if args.image else {"camera": args.camera},
                    "frame_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                    "warped_sha256": hashlib.sha256(warped.tobytes()).hexdigest(),
                    "inference_width": 960, "calibration": calibration,
                    "config_sha256": config_sha256, "model_sha256": model_sha256,
                    "runtime": {"python": sys.version, "opencv": cv2.__version__,
                                "numpy": np.__version__, "providers": model.session.get_providers()},
                    "layout": layout, "scores": np.asarray(scores).tolist(),
                    "board_pixel_corners": corners.tolist(),
                    "observation": observation,
                }, indent=2), encoding="utf-8")
                (folder / "layout.txt").write_text(layout + "\n", encoding="utf-8")
                with (folder / "cells.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["row", "column", "piece", "score"])
                    for row, line in enumerate(layout.splitlines()):
                        for col, piece in enumerate(line):
                            writer.writerow([row+1, col+1, piece, float(scores[row][col])])
                if reference is not None:
                    mismatches = [
                        {"row": row+1, "column": col+1, "expected": reference[row][col], "predicted": piece}
                        for row, line in enumerate(layout.splitlines())
                        for col, piece in enumerate(line) if piece != reference[row][col]
                    ]
                    comparison = {"matched": 90-len(mismatches), "total": 90, "mismatches": mismatches}
                    (folder / "comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
                    print(json.dumps(comparison), flush=True)
                print(f"Saved {folder}", flush=True)
            if key == ord("r"):
                corners = previous = None
                observations = ObservationStream(args.board_orientation, args.stable_frames, args.min_score)
                if args.latest_json:
                    invalidated = observations.observe(frame_id=frame_id, captured_at=captured_at, calibration={})
                    invalidated["reasons"].append("recalibration_requested")
                    write_latest(args.latest_json, invalidated)
                last_status = None
                if warped is not None and not args.no_preview:
                    # Clear the old board so a failed relocation cannot look current.
                    cv2.destroyWindow("Xiangqi recognition")
            if capture is None or key in (ord("q"), 27) or (args.frames and frame_id >= args.frames):
                break
    finally:
        try:
            if args.latest_json:
                stopped = ObservationStream(args.board_orientation, args.stable_frames, args.min_score).observe(
                    frame_id=frame_id, captured_at=captured_at, calibration={})
                stopped["reasons"].append("source_stopped")
                write_latest(args.latest_json, stopped)
        finally:
            if capture is not None:
                capture.release()
                cv2.destroyAllWindows()
    print(f"Processed {frame_id} frames; recognized {recognized_frames}", flush=True)
    if not recognized_frames:
        parser.exit(2, "No frames passed board localization.\n")


if __name__ == "__main__":
    main()
