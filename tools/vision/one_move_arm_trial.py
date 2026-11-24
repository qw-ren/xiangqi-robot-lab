"""Run one standard-opening camera-to-arm trial on the Raspberry Pi."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from xq_utils.rules import START_FEN, board_from_fen, checked_transition
from robot.arm_teaching.teach import taught_squares
from vision.board_evidence import BoardEvidence


def layout(fen):
    board = board_from_fen(fen)
    return "\n".join("".join(board.get(f"{col},{row}", ".") for col in range(9))
                     for row in range(10))


def wait_for_layout(expected, deadline, phase="Waiting for board", prior=None, required=()):
    return wait_for_matching_layout({expected}, deadline, phase, prior=prior, required=required)


def wait_for_matching_layout(expected, deadline, phase="Waiting for board", prior=None, required=(),
                             validate_layout=None):
    consecutive = 0
    last_frame = None
    previous_layout = None
    last_observation = {}
    evidence = BoardEvidence(prior, expected, required) if prior is not None else None
    request = urllib.request.Request(
        os.getenv("CHRO_VISION_SERVICE_URL", "http://127.0.0.1:5000").rstrip("/")
        + "/api/vision/observation",
        data=b'{"camera_index":0}', headers={"Content-Type": "application/json"},
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=min(10, max(0.1, deadline-time.monotonic()))) as response:
                observation = json.load(response).get("observation", {})
        except (URLError, TimeoutError, socket.timeout):
            observation = {}
        if observation:
            last_observation = observation
        frame = observation.get("frame_id")
        observed_layout = observation.get("layout")
        valid_aruco = frame is not None and frame != last_frame and observation.get("source") == "aruco"
        if evidence is not None:
            if valid_aruco:
                matched = evidence.add(observation.get("cells", ()))
                if matched is not None:
                    return matched
            else:
                evidence.reset()
        elif (valid_aruco and observed_layout
                and (validate_layout(observed_layout) if expected is None else observed_layout in expected)
                and not set(observation.get("reasons", ())) & {"unknown_cells", "low_scores"}):
            consecutive = consecutive + 1 if observed_layout == previous_layout else 1
            if consecutive == 3:
                return observed_layout
        else:
            consecutive = 0
        last_frame = frame
        previous_layout = observed_layout
        time.sleep(1)
    source = last_observation.get("source") or "none"
    missing = last_observation.get("calibration", {}).get("missing_ids", [])
    reasons = ", ".join(last_observation.get("reasons", [])) or "none"
    progress = f"window={len(evidence.frames)}/5" if evidence is not None else f"streak={consecutive}/3"
    details = f"source={source}; missing markers={missing}; reasons={reasons}; {progress}"
    actual = "".join((last_observation.get("layout") or "").splitlines())
    if len(actual) == 90 and expected:
        closest = min(("".join(item.splitlines()) for item in expected),
                      key=lambda item: (sum(a != b for a, b in zip(item, actual)), item))
        differences = [f"{chr(97 + index % 9)}{9 - index // 9}:{want}->{seen}"
                       for index, (want, seen) in enumerate(zip(closest, actual)) if want != seen]
        if differences:
            details += "; closest expected board differences (expected->seen): " + ", ".join(differences[:12])
    requirement = "expected board was not seen on three consecutive frames"
    if evidence is not None:
        requirement = "stable legal transition was not confirmed"
    elif expected is None:
        requirement = "a valid starting layout was not seen on three consecutive frames"
    raise TimeoutError(f"{phase}: {requirement} ({details})")


def reachable_replies(fen, points):
    # Teaching names use ICCS ranks (red home = 0), unlike vision image rows.
    squares = taught_squares(points)
    candidates = []
    for start in squares:
        for target in squares:
            try:
                reply = checked_transition(fen, start + target)
            except ValueError:
                continue
            if reply["capture"] is None:
                candidates.append(reply)
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-dir", type=Path, required=True, help="Directory containing teach_points.json")
    parser.add_argument("--human-move", default="c3c4")
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--check", action="store_true", help="Check points and candidate moves without hardware")
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be positive")
    human = checked_transition(START_FEN, args.human_move)
    from robot.arm_teaching.teach import TeachController, PointStore, Electromagnet, arm_lock, duration
    with arm_lock():
        store = PointStore(args.arm_dir / "teach_points.json")
        candidates = reachable_replies(human["fen"], store.points)
        if not candidates:
            raise RuntimeError("No legal non-capture reply has both squares taught")
        reply = candidates[0]
        move = reply["move"]
        print("Reachable replies:", ", ".join(candidate["move"] for candidate in candidates))
        if "park" not in store.points:
            raise RuntimeError("No park pose. Record it with the teaching command: teach park")
        if args.check:
            print("Point data ready; selected", move)
            return

        deadline = time.monotonic() + args.seconds
        print("Waiting for standard opening")
        wait_for_layout(layout(START_FEN), deadline)
        print("Opening confirmed; waiting for red", args.human_move)
        wait_for_layout(layout(human["fen"]), deadline)
        print("Detected red", args.human_move, "; selected", move)
        from robot.arm_teaching.Arm_Lib import Arm_Device
        arm = Arm_Device()
        magnet = Electromagnet()
        try:
            TeachController(arm, store, electromagnet=magnet).transfer(
                move[:2], move[2:], duration(), return_to_park=True)
        finally:
            magnet.close()
        print("Transfer complete and arm parked; verifying board")
        wait_for_layout(layout(reply["fen"]), time.monotonic() + 30)
        print("Expected board confirmed; disarmed")


if __name__ == "__main__":
    main()
