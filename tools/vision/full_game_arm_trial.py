"""Play from the standard opening or an existing board with the taught arm."""

import argparse
from collections import Counter
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.vision.one_move_arm_trial import layout, wait_for_layout, wait_for_matching_layout
from cchess import ChessBoard
from xq_utils.rules import PIECE_CODES, START_FEN, board_from_fen, checked_transition, fen_from_board
from robot.arm_teaching.teach import (
    BOARD_SQUARES, CAPTURE_SLOTS, Electromagnet, PointStore, TeachController, TeachError,
    arm_lock, capture_poses, duration,
)

PIECE_VALUES = {"r": 9, "c": 4, "n": 4, "b": 2, "a": 2, "p": 1}


def legal_transitions(fen):
    for start, end in ChessBoard(fen).create_moves():
        move = f"{chr(97 + start[0])}{start[1]}{chr(97 + end[0])}{end[1]}"
        try:
            yield checked_transition(fen, move)
        except ValueError:
            continue


def select_reply(fen, moves, engine):
    if engine is None:
        return min(moves, key=lambda plan: (
            -PIECE_VALUES.get((plan["capture"] or "").lower(), 0), plan["move"]))
    engine.set_position(fen, [])
    selected = engine.get_best_move()
    for plan in moves:
        if plan["move"] == selected:
            return plan
    raise RuntimeError(f"Engine did not return a legal move: {selected!r}")


def replay_moves(moves):
    fen = START_FEN
    captures = 0
    for move in moves:
        plan = checked_transition(fen, move)
        captures += fen.split()[1] == "b" and plan["capture"] is not None
        fen = plan["fen"]
    return fen, captures


def endgame_fen(observed: str, turn: str) -> str:
    rows = observed.splitlines()
    if len(rows) != 10 or any(len(row) != 9 or set(row) - set(PIECE_CODES + ".") for row in rows):
        raise ValueError("Invalid board layout")
    board = {f"{col},{row}": piece for row, rank in enumerate(rows)
             for col, piece in enumerate(rank) if piece != "."}
    if Counter(board.values()) - Counter(board_from_fen(START_FEN).values()):
        raise ValueError("Too many pieces of one type")
    for piece, ranks in (("k", range(3)), ("K", range(7, 10))):
        if not any(board.get(f"{col},{row}") == piece for row in ranks for col in range(3, 6)):
            raise ValueError("Both generals must be in their palaces")
    fen = fen_from_board(board, turn, 0, 1)
    if ChessBoard(fen).is_checking():
        raise ValueError("The side waiting to move cannot already be in check")
    return fen


def read_endgame(turn: str, seconds: int) -> str:
    def is_valid(observed: str) -> bool:
        try:
            endgame_fen(observed, turn)
        except ValueError:
            return False
        return True

    print("Reading the current board. Keep the arm at park and withdraw your hands.", flush=True)
    observed = wait_for_matching_layout(
        None, time.monotonic() + seconds, "Endgame setup", validate_layout=is_valid)
    fen = endgame_fen(observed, turn)
    print(f"Starting layout (black side on top)\n{observed}\nFEN {fen}", flush=True)
    return fen


def available_moves(fen: str) -> list:
    moves = list(legal_transitions(fen))
    if not moves:
        position = ChessBoard(fen)
        red_lost = fen.split()[1] == "w"
        position.move_player.next()
        reason = "checkmate" if position.is_checking() else "stalemate"
        winner = "Black" if red_lost else "Red"
        print(f"Game over: {winner} wins by {reason}.", flush=True)
    return moves


def post_transfer_layouts(reply):
    """Accept the exact black position or its exact legal red successor."""
    expected = {layout(reply["fen"]): reply["fen"]}
    for red in legal_transitions(reply["fen"]):
        expected[layout(red["fen"])] = red["fen"]
    return expected


def play(controller, engine, seconds, fen=START_FEN, endgame_turn=None, captures=0):
    if endgame_turn is not None:
        fen = read_endgame(endgame_turn, seconds)
    else:
        phase = "Opening check" if fen == START_FEN else "Resume position check"
        print(f"{phase}. Keep the arm at park.", flush=True)
        wait_for_layout(layout(fen), time.monotonic() + seconds, phase)
    while True:
        legal = available_moves(fen)
        if not legal:
            return
        if fen.split()[1] == "w":
            by_layout = {layout(plan["fen"]): plan for plan in legal}
            print("Red to move. Withdraw your hand after placing the piece.", flush=True)
            observed = wait_for_matching_layout(
                by_layout, time.monotonic() + seconds, "Red move check", prior=layout(fen))
            human = by_layout[observed]
            fen = human["fen"]
            print(f"Red played {human['move']}.", flush=True)
            legal = available_moves(fen)
            if not legal:
                return

        reply = select_reply(fen, legal, engine)
        move = reply["move"]
        print(f"Black selected {move}.", flush=True)
        transfer_prior = layout(fen)
        if reply["capture"] is not None:
            if captures >= len(CAPTURE_SLOTS):
                raise RuntimeError("The capture area is full")
            slot = CAPTURE_SLOTS[captures]
            required = tuple(row * 9 + col for row, col in (reply["source"], reply["target"]))
            wait_for_layout(transfer_prior, time.monotonic() + seconds,
                            f"Pre-capture check for {move}", prior=transfer_prior, required=required)
            controller.transfer(move[2:], slot, duration(), return_to_park=True)
            captures += 1
            print(f"Capture deposited in {slot}.", flush=True)
            board = board_from_fen(fen)
            row, col = reply["target"]
            del board[f"{col},{row}"]
            cleared = fen_from_board(board, "b", 0, 1)
            cleared_layout = layout(cleared)
            wait_for_layout(cleared_layout, time.monotonic() + seconds,
                            f"Capture removal for {move}", prior=transfer_prior)
            transfer_prior = cleared_layout
        controller.transfer(move[:2], move[2:], duration(), return_to_park=True)
        print("Arm parked. Verifying the next position.", flush=True)
        expected = post_transfer_layouts(reply)
        observed = wait_for_matching_layout(
            expected, time.monotonic() + 45, f"Post-transfer check for {move}", prior=transfer_prior)
        fen = expected[observed]
        if fen != reply["fen"]:
            red = next(plan for plan in legal_transitions(reply["fen"]) if plan["fen"] == fen)
            print(f"Black move verified; red already played {red['move']}.", flush=True)


def check_points(store):
    required = ["park"]
    required.extend(square + suffix for square in BOARD_SQUARES for suffix in ("a", "g"))
    required.extend(name for slot in CAPTURE_SLOTS for name in capture_poses(slot))
    missing = [name for name in required if name not in store.points]
    if missing:
        raise RuntimeError(f"{len(missing)} missing poses: {', '.join(missing[:12])}. "
                           "Use 'coverage' in the teaching console to list all missing poses.")


def main():
    os.environ["CHRO_VISION_SERVICE_URL"] = "http://127.0.0.1:5001"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-dir", type=Path, default=Path.home() / "chess_arm")
    parser.add_argument("--engine", type=Path, help="Optional path to a Pikafish executable")
    parser.add_argument("--camera", type=int, help="Override automatic local camera selection")
    parser.add_argument("--seconds", type=int, default=240, help="Timeout per human step")
    parser.add_argument("--resume-moves", nargs="+", default=[], metavar="ICCS",
                        help="Replay already executed moves from the opening before checking the physical board")
    parser.add_argument("--mode", choices=("standard", "endgame"), help="Skip the startup mode prompt")
    parser.add_argument("--turn", choices=("red", "black"), help="First side to move in endgame mode")
    parser.add_argument("--capture-start", type=int, choices=range(1, 18), metavar="SLOT",
                        help="Next free capture slot, from 1 to 16 (17 means full)")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be positive")
    try:
        fen, captures = replay_moves(args.resume_moves)
    except ValueError as exc:
        parser.error(f"Invalid resume history: {exc}")
    store = PointStore(args.arm_dir / "teach_points.json")
    check_points(store)
    print("All 90 square approaches and grab poses, park, and 16 capture slots are registered.", flush=True)
    if args.resume_moves:
        print("Expected resume layout (black side on top):\n" + layout(fen), flush=True)
    if not args.execute:
        return
    if args.resume_moves and (args.mode == "endgame" or args.turn is not None):
        parser.error("--resume-moves requires standard mode")
    mode = args.mode
    if mode is None:
        if args.resume_moves:
            mode = "standard"
        else:
            selected = input("Start [1] standard opening or [2] existing endgame: ").strip()
            if selected not in ("1", "2"):
                parser.error("Choose 1 or 2")
            mode = "standard" if selected == "1" else "endgame"
    endgame_turn = None
    if mode == "endgame":
        turn = args.turn or input("Side to move [r] red or [b] black: ").strip().lower()
        if turn not in ("red", "black", "r", "b"):
            parser.error("Choose r or b")
        endgame_turn = "w" if turn in ("red", "r") else "b"
    elif args.turn is not None:
        parser.error("--turn requires endgame mode")
    if args.capture_start is not None:
        captures = args.capture_start - 1
    print(f"Capture storage starts at slot {captures + 1} (17 means full).", flush=True)

    from vision.server import default_camera_index, game_vision
    camera = args.camera if args.camera is not None else default_camera_index()
    with arm_lock(), game_vision(camera):
        print(f"Vision started on camera {camera}.", flush=True)
        # Reload after locking so no teaching session can race the preflight.
        store = PointStore(store.path)
        check_points(store)
        engine = None
        magnet = None
        try:
            if args.engine is not None:
                from ai.engine import AIEngine
                engine = AIEngine(str(args.engine.resolve()))
                if not engine.start():
                    raise RuntimeError("Pikafish failed to start")
            else:
                print("Using the built-in capture-priority trial policy.", flush=True)
            from robot.arm_teaching.Arm_Lib import Arm_Device
            arm = Arm_Device()
            magnet = Electromagnet()
            play(TeachController(arm, store, electromagnet=magnet), engine, args.seconds,
                 fen, endgame_turn=endgame_turn, captures=captures)
        finally:
            try:
                if magnet is not None:
                    magnet.close()
            finally:
                if engine is not None:
                    engine.stop()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, TimeoutError, EOFError, TeachError) as exc:
        sys.exit(f"Stopped: {exc}")
