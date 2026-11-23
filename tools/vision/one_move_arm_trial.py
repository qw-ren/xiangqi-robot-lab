"""Run one standard-opening camera-to-arm trial on the Raspberry Pi."""

import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from xq_utils.rules import START_FEN, board_from_fen, checked_transition
from robot.arm_teaching.teach import taught_squares


def layout(fen):
    board = board_from_fen(fen)
    return "\n".join("".join(board.get(f"{col},{row}", ".") for col in range(9))
                     for row in range(10))


def wait_for_layout(expected, deadline):
    consecutive = 0
    last_frame = None
    request = urllib.request.Request(
        "http://127.0.0.1:5000/api/vision/observation",
        data=b'{"camera_index":0}', headers={"Content-Type": "application/json"},
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=min(10, max(0.1, deadline-time.monotonic()))) as response:
                observation = json.load(response).get("observation", {})
        except (URLError, TimeoutError):
            observation = {}
        frame = observation.get("frame_id")
        if (frame != last_frame and observation.get("source") == "aruco"
                and observation.get("layout") == expected
                and not set(observation.get("reasons", ())) & {"unknown_cells", "low_scores"}):
            consecutive += 1
            if consecutive == 3:
                return
        else:
            consecutive = 0
        last_frame = frame
        time.sleep(1)
    raise TimeoutError("Expected board was not seen on three consecutive frames")


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
