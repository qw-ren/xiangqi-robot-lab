"""Play from the standard opening or an existing board with the taught arm."""

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime
from functools import partial
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.vision.one_move_arm_trial import (
    OPENING_REVIEW_FRAMES, layout, starting_cell_needs_review, wait_for_layout,
    wait_for_matching_layout,
)
from cchess import ChessBoard
from game_manager.game_journal import GameJournal
from xq_utils.rules import PIECE_CODES, START_FEN, board_from_fen, checked_transition, fen_from_board
from robot.arm_teaching.teach import (
    APPROACH_POINTS, BOARD_SQUARES, CAPTURE_SLOTS, Electromagnet, PointStore,
    TeachController, TeachError, arm_lock, capture_poses,
)

PIECE_VALUES = {"r": 9, "c": 4, "n": 4, "b": 2, "a": 2, "p": 1}
PIECE_NAMES = dict(zip(".xrnbakcpRNBAKCP", (
    "空位", "未知", "黑车", "黑马", "黑象", "黑士", "黑将", "黑炮", "黑卒",
    "红车", "红马", "红相", "红仕", "红帅", "红炮", "红兵",
)))


@contextmanager
def vision_log(path: Path):
    loggers = [logging.getLogger(name) for name in ("vision", "core", "werkzeug")]
    settings = [(logger.level, logger.propagate) for logger in loggers]
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    try:
        for logger in loggers:
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        yield
    finally:
        for logger, (level, propagate) in zip(loggers, settings):
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate
        handler.close()


def web_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5000), timeout=0.2):
            return True
    except OSError:
        return False


@contextmanager
def game_web(path: Path, journal_path: Path):
    if web_port_open():
        print("Web port 5000 is already in use. Close the old Web process to restart it.", flush=True)
        yield
        return

    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, CHRO_SKIP_STARTUP_PROMPT="1", CHRO_GAME_JOURNAL=str(journal_path.resolve()))
    with path.open("ab") as log:
        web = subprocess.Popen(
            [sys.executable, "-u", "web_simulation/app.py"], cwd=root, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while web.poll() is None and time.monotonic() < deadline:
                if web_port_open():
                    print("Web preview ready on port 5000.", flush=True)
                    break
                time.sleep(0.1)
            else:
                print(f"Web startup not confirmed. See {path}", flush=True)
            yield
        finally:
            if web.poll() is None:
                web.terminate()
                try:
                    web.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    web.kill()
                    web.wait()


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


def confirm_starting_position(observed, cells, *, expected=None, validate_layout=None,
                              first_side=None, journal=None):
    expected_rows = expected.splitlines() if expected is not None else None
    notices = []
    for cell in cells:
        row, col, piece = cell["row"], cell["col"], cell["piece"]
        square = f"{chr(97 + col)}{9 - row}"
        name = PIECE_NAMES.get(piece, piece)
        wanted = expected_rows[row][col] if expected_rows is not None else piece
        if wanted != piece:
            notices.append(f"{square} 应为{PIECE_NAMES.get(wanted, wanted)}，识别为{name}（{cell['score']:.2f}）")
        elif starting_cell_needs_review(cell):
            notices.append(f"{square} 未识别" if piece == "x" else f"{square} {name}（{cell['score']:.2f}）")
    detail = " · ".join(notices) or "识别仍有波动，请核对实物棋盘"
    print(f"已检查 {OPENING_REVIEW_FRAMES} 帧，请人工核对开局。\n{detail}", flush=True)
    if journal is not None:
        journal.status("review", detail)
    if validate_layout is not None and not validate_layout(observed):
        input("当前识别结果还无法组成有效残局，请检查实物棋盘后按回车重扫：")
        accepted = False
    else:
        board = f"当前残局和{first_side}先手设置" if first_side else "实物棋盘与预期局面"
        accepted = not input(f"确认{board}无误请按回车，输入任意字符后按回车重扫：").strip()
    if not accepted and journal is not None:
        journal.status("setup")
    return accepted


def read_endgame(turn: str, seconds: int, journal=None) -> str:
    first_side = "红方" if turn == "w" else "黑方"

    def is_valid(observed: str) -> bool:
        try:
            endgame_fen(observed, turn)
        except ValueError:
            return False
        return True

    print("正在识别残局。请让机械臂停在待机位置，并把手移出棋盘画面。", flush=True)
    observed = wait_with_operator(
        wait_for_matching_layout, None, seconds, "Endgame setup", validate_layout=is_valid,
        opening=True, starting_position=True, journal=journal,
        confirm_low_scores=partial(confirm_starting_position, validate_layout=is_valid,
                                   first_side=first_side, journal=journal))
    fen = endgame_fen(observed, turn)
    print(f"残局已确认（{first_side}先走）", flush=True)
    return fen


def available_moves(fen: str, journal=None) -> list:
    moves = list(legal_transitions(fen))
    if not moves:
        position = ChessBoard(fen)
        red_lost = fen.split()[1] == "w"
        position.move_player.next()
        reason = "checkmate" if position.is_checking() else "stalemate"
        winner = "Black" if red_lost else "Red"
        if journal is not None:
            journal.update(phase="finished", detail="", result=dict(winner=winner.lower(), reason=reason))
        print(f"Game over: {winner} wins by {reason}.", flush=True)
    return moves


def post_transfer_layouts(reply):
    """Accept the exact black position or its exact legal red successor."""
    expected = {layout(reply["fen"]): reply["fen"]}
    for red in legal_transitions(reply["fen"]):
        expected[layout(red["fen"])] = red["fen"]
    return expected


def wait_with_operator(check, expected, seconds, phase, *, opening=False, journal=None, **kwargs):
    """Retry in place. Only setup can pause for operator input."""
    if journal is not None:
        current_phase = journal.state["phase"]

        def report(frame, source, low, differences):
            if frame is None:
                detail = "等待摄像头提供新画面"
            elif source != "aruco":
                detail = "等待定位棋盘"
            elif differences or low:
                notices = []
                for item in differences:
                    square, pair = item.split(":")
                    wanted, seen = pair.split("->")
                    notices.append(f"等待 {square} 识别为{PIECE_NAMES.get(wanted, wanted)}，"
                                   f"当前识别为{PIECE_NAMES.get(seen, seen)}")
                for item in low:
                    square, reading = item.split(":")
                    piece, score = reading.split("@")
                    notices.append(f"{square} 未识别" if piece == "x" else
                                   f"{square} 的{PIECE_NAMES.get(piece, piece)}置信度不足（{score}）")
                detail = " · ".join(notices[:4])
            else:
                detail = ""
            if detail and journal.state["detail"] != detail:
                print(detail, flush=True)
            journal.status(current_phase, detail)

        kwargs["report"] = report
    while True:
        try:
            return check(expected, time.monotonic() + seconds, phase, **kwargs)
        except TimeoutError as exc:
            print(f"Paused: {exc}", flush=True)
            if journal is not None:
                journal.status(current_phase, "复核超时，等待重新确认")
            if opening:
                input("请检查实物棋盘和摄像头，按回车重试，或按 Ctrl+C 停止：")
            else:
                print("仍在等待视觉确认。请检查棋盘，修正后程序会自动继续。", flush=True)


def play(controller, engine, seconds, fen=START_FEN, endgame_turn=None, captures=0, journal=None,
         initial_moves=()):
    pending = journal.state["pending"] if journal is not None else None
    if pending:
        plan = checked_transition(fen, pending["move"])
        fen = plan["fen"]
        print(f"恢复中断的黑方走法 {pending['move']}。请核对机械臂已停稳，实物已摆成该步完成后的局面，"
              "若为吃子，还须核对对应吃子槽。", flush=True)
    if endgame_turn is not None:
        fen = read_endgame(endgame_turn, seconds, journal=journal)
    else:
        phase = "Opening check" if fen == START_FEN else "Resume position check"
        print(f"{phase}. Keep the arm at park.", flush=True)
        expected = layout(fen)
        wait_with_operator(wait_for_layout, expected, seconds, phase, opening=True,
                           starting_position=True, journal=journal,
                           confirm_low_scores=partial(confirm_starting_position, expected=expected, journal=journal))
    if journal is not None:
        if pending:
            journal.confirm(pending["move"])
        elif journal.state["fen"] is None:
            initial_fen = START_FEN if initial_moves else fen
            journal.confirm(*initial_moves, starting_fen=initial_fen, captures=captures)
    if journal is not None:
        journal.update(preflight=dict(journal.state.get("preflight", {}), opening="已确认"))
    controller.set_torque(True)
    print("已发送机械臂舵机使能指令。", flush=True)
    while True:
        legal = available_moves(fen, journal)
        if not legal:
            controller.celebrate()
            return
        if fen.split()[1] == "w":
            if journal is not None:
                journal.status("red")
            by_layout = {layout(plan["fen"]): plan for plan in legal}
            print("Red to move. Withdraw your hand after placing the piece.", flush=True)
            observed = wait_with_operator(
                wait_for_matching_layout, by_layout, seconds, "Red move check", prior=layout(fen), journal=journal)
            human = by_layout[observed]
            fen = human["fen"]
            if journal is not None:
                journal.confirm(human["move"])
            print(f"Red played {human['move']}.", flush=True)
            legal = available_moves(fen, journal)
            if not legal:
                controller.celebrate()
                return

        if journal is not None:
            journal.status("thinking")
        reply = select_reply(fen, legal, engine)
        move = reply["move"]
        print(f"Black selected {move}.", flush=True)
        transfer_prior = layout(fen)
        capture = reply["capture"] is not None
        if journal is not None:
            journal.update(phase="selected", planned_move=move, detail="")
        if reply["capture"] is not None:
            if captures >= len(CAPTURE_SLOTS):
                raise RuntimeError("The capture area is full")
            slot = CAPTURE_SLOTS[captures]
            required = tuple(row * 9 + col for row, col in (reply["source"], reply["target"]))
            wait_with_operator(wait_for_layout, transfer_prior, seconds,
                               f"Pre-capture check for {move}",
                               prior=transfer_prior, required=required, journal=journal)
            if journal is not None:
                journal.update(phase="moving", pending=dict(move=move, slot=slot),
                               captures=captures + 1, detail=move)
            controller.transfer(move[2:], slot, return_to_park=True)
            captures += 1
            print(f"Capture deposited in {slot}.", flush=True)
            board = board_from_fen(fen)
            row, col = reply["target"]
            del board[f"{col},{row}"]
            cleared = fen_from_board(board, "b", 0, 1)
            cleared_layout = layout(cleared)
            if journal is not None:
                journal.status("clearing", move[2:])
            print(f"Arm parked. Waiting for vision to confirm {move[2:]} is clear.", flush=True)
            wait_with_operator(wait_for_layout, cleared_layout, seconds,
                               f"Capture removal for {move}",
                               prior=transfer_prior, journal=journal)
            transfer_prior = cleared_layout
        if journal is not None and not capture:
            journal.update(phase="moving", pending=dict(move=move, slot=None), detail=move)
        elif journal is not None:
            journal.status("moving", move)
        controller.transfer(move[:2], move[2:], return_to_park=True)
        if journal is not None:
            journal.status("verifying", move)
        print("Arm parked. Verifying the next position.", flush=True)
        expected = post_transfer_layouts(reply)
        row, col = reply["target"]
        observed = wait_with_operator(
            wait_for_matching_layout, expected, 45, f"Post-transfer check for {move}",
            prior=transfer_prior, robot_destination=row * 9 + col, journal=journal)
        fen = expected[observed]
        if fen != reply["fen"]:
            red = next(plan for plan in legal_transitions(reply["fen"]) if plan["fen"] == fen)
            if journal is not None:
                journal.confirm(move, red["move"])
            print(f"Black move verified; red already played {red['move']}.", flush=True)
        elif journal is not None:
            journal.confirm(move)


def check_points(store):
    required = ["park"]
    required.extend(APPROACH_POINTS)
    required.extend(square + suffix for square in BOARD_SQUARES for suffix in ("a", "g"))
    required.extend(name for slot in CAPTURE_SLOTS for name in capture_poses(slot))
    missing = [name for name in required if name not in store.points]
    if missing:
        raise RuntimeError(f"{len(missing)} missing poses: {', '.join(missing[:12])}. "
                           "Use 'coverage' in the teaching console to list all missing poses.")


def choose_opening_turn(args, parser) -> Optional[str]:
    if args.resume_moves and (args.mode == "endgame" or args.turn is not None):
        parser.error("--resume-moves requires standard mode")
    mode = args.mode
    if mode is None:
        if args.resume_moves:
            mode = "standard"
        else:
            selected = input("选择开局方式（1 标准开局，2 残局）：").strip()
            if selected not in ("1", "2"):
                parser.error("请选择 1 或 2")
            mode = "standard" if selected == "1" else "endgame"
    endgame_turn = None
    if mode == "endgame":
        turn = args.turn or input("选择先手（1 红方，2 黑方）：").strip().lower()
        if turn not in ("red", "black", "r", "b", "1", "2"):
            parser.error("请选择 1 或 2")
        endgame_turn = "w" if turn in ("red", "r", "1") else "b"
    elif args.turn is not None:
        parser.error("--turn requires endgame mode")
    return endgame_turn


def main():
    os.environ["CHRO_VISION_SERVICE_URL"] = "http://127.0.0.1:5001"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm-dir", type=Path, default=Path.home() / "chess_arm")
    parser.add_argument("--engine", type=Path, help="Optional path to a Pikafish executable")
    parser.add_argument("--camera", type=int, help="Override automatic local camera selection")
    parser.add_argument("--seconds", type=int, default=240, help="Timeout per human step")
    parser.add_argument("--resume-moves", nargs="+", default=[], metavar="ICCS",
                        help="Replay already executed moves from the opening before checking the physical board")
    parser.add_argument("--resume-game", type=Path, help="Resume a saved game after checking its physical board")
    parser.add_argument("--mode", choices=("standard", "endgame"), help="Skip the startup mode prompt")
    parser.add_argument("--turn", choices=("red", "black"), help="First side to move in endgame mode")
    parser.add_argument("--capture-start", type=int, choices=range(1, 18), metavar="SLOT",
                        help="Next free capture slot, from 1 to 16 (17 means full)")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error("--seconds must be positive")
    if args.resume_game and (args.resume_moves or args.mode or args.turn or args.capture_start is not None):
        parser.error("--resume-game already specifies the position, side to move and capture count")
    try:
        fen, captures = replay_moves(args.resume_moves)
    except ValueError as exc:
        parser.error(f"Invalid resume history: {exc}")
    store = PointStore(args.arm_dir / "teach_points.json")
    check_points(store)
    print("All 4 region approaches, 90 square approaches and grab poses, park, and 16 capture slots are registered.", flush=True)
    if args.resume_moves:
        print("Expected resume layout (black side on top):\n" + layout(fen), flush=True)
    if not args.execute:
        return
    from vision.runtime_diagnostics import record_runtime
    from vision.server import default_camera_link, game_vision
    camera_link = default_camera_link() if args.camera is None else None
    camera = int(camera_link.resolve().name[5:]) if camera_link is not None else (args.camera or 0)
    log_dir = args.arm_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    journal_path = args.resume_game or args.arm_dir / "games" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
    with arm_lock(), record_runtime(log_dir / "diagnostics.log", camera), vision_log(
        log_dir / "vision.log"
    ), game_web(log_dir / "web.log", journal_path), ExitStack() as resources:
        # Reload after locking so no teaching session can race the preflight.
        store = PointStore(store.path)
        check_points(store)
        try:
            journal = GameJournal.load(journal_path) if args.resume_game else GameJournal(journal_path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"无法读取续局存档 {journal_path}: {exc}")
        checks = dict(points="已核对", camera="待检查", engine="待检查", opening="待确认")
        journal.update(phase="setup", detail="", preflight=checks)
        if args.resume_game:
            fen, captures = journal.state["fen"], journal.state["captures"]
        elif args.capture_start is not None:
            captures = args.capture_start - 1
        endgame_turn = None if args.resume_game else choose_opening_turn(args, parser)
        print(f"对局存档 {journal_path}。下一个吃子槽为 {captures + 1}（17 表示已满）。", flush=True)
        engine = None
        magnet = None
        try:
            resources.enter_context(game_vision(camera, camera_link=camera_link))
            print(f"Vision started on camera {camera}.", flush=True)
            checks = dict(checks, camera=f"已开启（设备 {camera}）")
            journal.update(preflight=checks)
            if args.engine is not None:
                from ai.engine import AIEngine
                engine = AIEngine(str(args.engine.resolve()))
                if not engine.start():
                    raise RuntimeError("Pikafish failed to start")
            else:
                print("Using the built-in capture-priority trial policy.", flush=True)
            checks = dict(checks, engine="Pikafish 已启动" if engine else "内置策略已就绪")
            journal.update(preflight=checks)
            from robot.arm_teaching.Arm_Lib import Arm_Device
            arm = Arm_Device()
            magnet = Electromagnet()
            play(TeachController(arm, store, electromagnet=magnet), engine, args.seconds,
                 fen, endgame_turn=endgame_turn, captures=captures, journal=journal,
                 initial_moves=args.resume_moves)
        except (Exception, KeyboardInterrupt) as exc:
            if journal.state["result"] is None:
                journal.status("stopped", str(exc) or "对局已停止")
            raise
        finally:
            try:
                if magnet is not None:
                    magnet.close()
            finally:
                if engine is not None:
                    engine.stop()
        if journal.state["result"] is not None:
            try:
                input("对局已结束。按回车关闭展示页面服务。")
            except EOFError:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Stopped by operator.")
    except (RuntimeError, TimeoutError, EOFError, TeachError) as exc:
        sys.exit(f"Stopped: {exc}")
