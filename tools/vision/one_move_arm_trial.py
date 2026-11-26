"""Run one standard-opening camera-to-arm trial on the Raspberry Pi."""

import argparse
import json
import logging
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
from vision.board_evidence import BoardEvidence, cell_score_threshold

diagnostics = logging.getLogger("chro.diagnostics")
STARTING_CELL_SCORE = 0.75
OPENING_REVIEW_FRAMES = 12


def starting_cell_needs_review(cell):
    piece = cell["piece"]
    return piece == "x" or (piece != "." and cell["score"] < STARTING_CELL_SCORE)


def starting_scores_ok(observation):
    cells = observation.get("cells", ())
    return ("unknown_cells" not in observation.get("reasons", ()) and len(cells) == 90
            and not any(starting_cell_needs_review(cell) for cell in cells))


def layout(fen):
    board = board_from_fen(fen)
    return "\n".join("".join(board.get(f"{col},{row}", ".") for col in range(9))
                     for row in range(10))


def wait_for_layout(expected, deadline, phase="Waiting for board", prior=None, required=(),
                    starting_position=False, report=None, confirm_low_scores=None):
    return wait_for_matching_layout({expected}, deadline, phase, prior=prior, required=required,
                                    starting_position=starting_position, report=report,
                                    confirm_low_scores=confirm_low_scores)


def wait_for_matching_layout(expected, deadline, phase="Waiting for board", prior=None, required=(),
                             validate_layout=None, robot_destination=None, starting_position=False,
                             confirm_low_scores=None, report=None):
    consecutive = 0
    review_frames = 0
    review_needed = False
    retry_seconds = max(0.0, deadline - time.monotonic())
    last_frame = None
    previous_layout = None
    last_observation = {}
    evidence = BoardEvidence(prior, expected, required, robot_destination) if prior is not None else None
    last_error = None
    next_report = 0.0
    next_camera_notice = 0.0
    next_aruco_notice = 0.0
    diagnostics.info("Board check started: %s", phase)
    request = urllib.request.Request(
        os.getenv("CHRO_VISION_SERVICE_URL", "http://127.0.0.1:5000").rstrip("/")
        + "/api/vision/observation",
        data=b'{"camera_index":0}', headers={"Content-Type": "application/json"},
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=min(10, max(0.1, deadline-time.monotonic()))) as response:
                observation = json.load(response).get("observation", {})
        except (URLError, TimeoutError, socket.timeout) as exc:
            last_error = str(exc)
            observation = {}
        else:
            last_error = None
        if observation:
            last_observation = observation
        frame = observation.get("frame_id")
        observed_layout = observation.get("layout")
        now = time.monotonic()
        if frame is None and now >= next_camera_notice:
            detail = last_error or "empty vision response"
            message = ("no fresh camera frame received" if "503" in detail
                       else "vision did not return an observation")
            print(f"{phase}: {message} ({detail}). Waiting for vision.", flush=True)
            next_camera_notice = now + 10
        elif frame is not None and next_camera_notice:
            print(f"{phase}: vision observations received again.", flush=True)
            next_camera_notice = 0.0
        if frame is not None and observation.get("source") != "aruco" and now >= next_aruco_notice:
            missing = observation.get("calibration", {}).get("missing_ids", [])
            print(f"{phase}: camera is delivering frames, but ArUco localization failed "
                  f"(missing markers: {missing}).", flush=True)
            next_aruco_notice = now + 10
        elif observation.get("source") == "aruco":
            next_aruco_notice = 0.0
        if now >= next_report:
            threshold = observation.get("quality", {}).get("min_score", 0.8)
            cells = observation.get("cells", ())
            actual = "".join((observed_layout or "").splitlines())
            relevant = evidence.closest_changes(actual) if evidence is not None and len(actual) == 90 else None
            low = [f"{chr(97 + cell['col'])}{9 - cell['row']}:{cell['piece']}@{cell['score']:.2f}"
                   for cell in cells if (relevant is None or cell["row"] * 9 + cell["col"] in relevant)
                   and (cell["piece"] == "x" or (
                       starting_cell_needs_review(cell) if starting_position else cell["score"] < (
                           cell_score_threshold(cell["piece"]) if evidence is not None else threshold
                       )
                   ))]
            differences = []
            if relevant is not None:
                differences = [f"{chr(97 + i % 9)}{9 - i // 9}:{want}->{actual[i]}"
                               for i, want in relevant.items() if want != actual[i]]
            elif len(actual) == 90 and expected:
                closest = min(("".join(candidate.splitlines()) for candidate in expected),
                              key=lambda candidate: sum(a != b for a, b in zip(candidate, actual)))
                differences = [f"{chr(97 + index % 9)}{9 - index // 9}:{want}->{seen}"
                               for index, (want, seen) in enumerate(zip(closest, actual)) if want != seen]
            diagnostics.info("Board check %s frame=%s source=%s low=%s differences=%s request_error=%s",
                             phase, frame, observation.get("source"), low[:12], differences[:12], last_error)
            if report is not None:
                report(frame, observation.get("source"), low[:4], differences[:4])
            next_report = now + 10
        if frame is not None and frame == last_frame:
            time.sleep(1)
            continue
        valid_aruco = frame is not None and observation.get("source") == "aruco"
        if evidence is not None:
            if valid_aruco:
                matched = evidence.add(observation.get("cells", ()))
                if matched is not None:
                    return matched
            else:
                evidence.reset()
        elif valid_aruco and observed_layout:
            layout_matches = ("unknown_cells" not in observation.get("reasons", ())
                              and (validate_layout(observed_layout) if expected is None
                                   else observed_layout in expected))
            scores_ok = (starting_scores_ok(observation) if starting_position else
                         "low_scores" not in observation.get("reasons", ()))
            same_board = observed_layout == previous_layout
            consecutive = (consecutive + 1 if same_board else 1) if layout_matches and scores_ok else 0
            if consecutive == 3:
                return observed_layout
            if confirm_low_scores is not None and len(observation.get("cells", ())) == 90:
                review_frames += 1
                review_needed = review_needed or not scores_ok
                if review_frames >= OPENING_REVIEW_FRAMES and review_needed:
                    if confirm_low_scores(observed_layout, observation["cells"]):
                        return next(iter(expected)) if expected and len(expected) == 1 else observed_layout
                    consecutive = review_frames = 0
                    review_needed = False
                    deadline = time.monotonic() + retry_seconds
        else:
            consecutive = 0
        if frame is not None:
            last_frame = frame
        previous_layout = observed_layout
        time.sleep(1)
    source = last_observation.get("source") or "none"
    missing = last_observation.get("calibration", {}).get("missing_ids", [])
    reasons = ", ".join(last_observation.get("reasons", [])) or "none"
    progress = f"window={len(evidence.frames)}/5" if evidence is not None else f"streak={consecutive}/3"
    details = f"source={source}; missing markers={missing}; reasons={reasons}; {progress}"
    if last_error:
        details += f"; latest vision request error: {last_error}"
    actual = "".join((last_observation.get("layout") or "").splitlines())
    if len(actual) == 90 and evidence is not None:
        changes = evidence.closest_changes(actual)
        details += "; relevant squares (expected->seen): " + ", ".join(
            f"{chr(97 + i % 9)}{9 - i // 9}:{want}->{actual[i]}" for i, want in changes.items())
    elif len(actual) == 90 and expected:
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
    from robot.arm_teaching.teach import TeachController, PointStore, Electromagnet, arm_lock
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
        wait_for_layout(layout(START_FEN), deadline, starting_position=True)
        print("Opening confirmed; waiting for red", args.human_move)
        wait_for_layout(layout(human["fen"]), deadline)
        print("Detected red", args.human_move, "; selected", move)
        from robot.arm_teaching.Arm_Lib import Arm_Device
        arm = Arm_Device()
        magnet = Electromagnet()
        try:
            TeachController(arm, store, electromagnet=magnet).transfer(
                move[:2], move[2:], return_to_park=True)
        finally:
            magnet.close()
        print("Transfer complete and arm parked; verifying board")
        wait_for_layout(layout(reply["fen"]), time.monotonic() + 30)
        print("Expected board confirmed; disarmed")


if __name__ == "__main__":
    main()
