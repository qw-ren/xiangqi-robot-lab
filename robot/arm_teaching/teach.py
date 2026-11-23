#!/usr/bin/env python3
"""Interactive five-servo teaching over the Yahboom extension board."""

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time

JOINT_IDS = (1, 2, 3, 4, 5)
DEFAULT_DURATION_MS = 1000
WRITE_GAP_SECONDS = 0.01
ELECTROMAGNET_GPIO = 21
MOVE_SETTLE_SECONDS = 0.15
MAGNET_PICKUP_SECONDS = 1.0
MAGNET_RELEASE_SECONDS = 0.25
APPROACH_POINTS = ("approach_red_left", "approach_red_right",
                   "approach_black_left", "approach_black_right")


def quadrant_approach(square: str) -> str:
    """Use ICCS ranks and a–i column order. The center file e belongs to a–e."""
    return APPROACH_POINTS[2 * (int(square[1]) >= 5) + (square[0] >= "f")]


def square_approach(square: str, points: dict) -> str:
    if any(name in points for name in APPROACH_POINTS):
        return quadrant_approach(square)
    return square + "a"


def taught_squares(points: dict):
    return [square for col in "abcdefghi" for rank in range(10)
            for square in (f"{col}{rank}",)
            if square + "g" in points and square_approach(square, points) in points]


class TeachError(Exception):
    pass


@contextmanager
def arm_lock():
    """Keep the teaching console and integrated runner off the bus simultaneously."""
    import fcntl

    with open("/tmp/xiangqi-arm-one-move.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TeachError("The arm is already reserved by another session.") from exc
        yield


class Electromagnet:
    def __init__(self, gpio=None):
        if gpio is None:
            try:
                import RPi.GPIO as gpio
            except ImportError as exc:
                raise RuntimeError("RPi.GPIO is required for electromagnet control: {}".format(exc))
        self.gpio = gpio
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setup(
            ELECTROMAGNET_GPIO,
            self.gpio.OUT,
            initial=self.gpio.LOW,
        )
        self.is_on = False

    def set(self, enabled):
        self.gpio.output(
            ELECTROMAGNET_GPIO,
            self.gpio.HIGH if enabled else self.gpio.LOW,
        )
        self.is_on = bool(enabled)

    def close(self):
        self.set(False)


def number(value, label):
    if isinstance(value, bool):
        raise TeachError("{} must be a finite number.".format(label))
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise TeachError("{} must be a finite number.".format(label))
    if not math.isfinite(result):
        raise TeachError("{} must be a finite number.".format(label))
    return result


def servo_id(value):
    if str(value) not in tuple(str(i) for i in JOINT_IDS):
        raise TeachError("Servo ID must be 1, 2, 3, 4 or 5.")
    return int(value)


def duration(value=None):
    if value is None:
        return DEFAULT_DURATION_MS
    result = number(value, "Duration")
    if not result.is_integer() or not 0 <= result <= 65535:
        raise TeachError("Duration must be an integer from 0 to 65535 ms.")
    return int(result)


def point_name(value):
    name = str(value).strip()
    if not name or len(name) > 40 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name):
        raise TeachError("Point name may contain letters, numbers, _ and -.")
    return name


def format_angle(value):
    return "UNAVAILABLE" if value is None else "{:.0f}".format(value)


class PointStore:
    def __init__(self, path):
        self.path = Path(path)
        self.points = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (not isinstance(data, dict) or data.get("version") != 1
                        or not isinstance(data.get("points"), dict)
                        or set(data) != {"version", "points"}):
                    raise ValueError("unexpected file structure")
                for name, pose in data["points"].items():
                    point_name(name)
                    if not isinstance(pose, list) or len(pose) != 5:
                        raise ValueError("each point must contain five angles")
                    data["points"][name] = [number(v, "Stored angle") for v in pose]
                self.points = data["points"]
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise TeachError("Cannot load point file {}: {}".format(self.path, exc))

    def save(self, name, pose):
        self.save_many({name: pose})

    def save_many(self, poses):
        updated = dict(self.points)
        for name, pose in poses.items():
            values = [number(value, "Angle") for value in pose]
            if len(values) != 5:
                raise TeachError("A saved pose must contain five angles.")
            updated[point_name(name)] = values
        self._write(updated)

    def _write(self, updated):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", suffix=".tmp",
                    dir=str(self.path.parent), delete=False) as stream:
                temporary = stream.name
                json.dump({"version": 1, "points": updated}, stream,
                          indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, str(self.path))
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        self.points = updated

    def delete(self, name):
        name = point_name(name)
        if name not in self.points:
            raise TeachError("Point {} has not been saved.".format(name))

        updated = dict(self.points)
        del updated[name]
        self._write(updated)


class TeachController:
    def __init__(self, arm, store, electromagnet=None, emit=print):
        self.arm = arm
        self.store = store
        self.electromagnet = electromagnet
        self.emit = emit

    def read_one(self, servo):
        try:
            value = self.arm.Arm_serial_servo_read(servo)
            if value is None:
                return None
            return number(value, "Feedback")
        except Exception as exc:
            self.emit("ID {} read failed: {}".format(servo, exc))
            return None

    def read_pose(self):
        pose = [self.read_one(servo) for servo in JOINT_IDS]
        self.emit("  ".join(
            "J{}={}".format(servo, format_angle(value))
            for servo, value in zip(JOINT_IDS, pose)))
        return pose

    def send_one(self, servo, angle, move_time):
        angle = number(angle, "Angle")
        self.arm.Arm_serial_servo_write(servo, angle, move_time)
        self.emit("ID {} target: {:.0f} deg, {} ms".format(
            servo, float(angle), move_time))

    def send_pose(self, pose, move_time):
        if len(pose) != 5:
            raise TeachError("A pose must contain five angles in ID order.")
        values = [number(value, "Angle") for value in pose]
        for index, (servo, angle) in enumerate(zip(JOINT_IDS, values)):
            self.send_one(servo, angle, move_time)
            if index + 1 < len(JOINT_IDS):
                time.sleep(WRITE_GAP_SECONDS)

    def move_to_point(self, name, move_time):
        if name not in self.store.points:
            raise TeachError("Point {} has not been saved.".format(name))
        self.emit("Moving to {}.".format(name))
        self.send_pose(self.store.points[name], move_time)
        time.sleep(move_time / 1000.0 + MOVE_SETTLE_SECONDS)

    def transfer(self, source, destination, move_time, return_to_park=False):
        source = self.board_coordinate(source)
        destination = self.board_coordinate(destination)
        source_approach = square_approach(source, self.store.points)
        destination_approach = square_approach(destination, self.store.points)
        point_names = (source_approach, source + "g", destination_approach, destination + "g")
        if return_to_park:
            point_names += ("park",)
        missing = [name for name in dict.fromkeys(point_names) if name not in self.store.points]
        if missing:
            raise TeachError("Transfer not started; missing points: {}".format(
                ", ".join(missing)))

        self.emit("Transfer {} -> {} ({} ms per move).".format(
            source, destination, move_time))
        self.move_to_point(source_approach, move_time)
        self.move_to_point(source + "g", move_time)

        magnet_on = False
        try:
            self.set_magnet(True)
            magnet_on = True
            time.sleep(MAGNET_PICKUP_SECONDS)
            self.move_to_point(source_approach, move_time)
            if destination_approach != source_approach:
                self.move_to_point(destination_approach, move_time)
            self.move_to_point(destination + "g", move_time)
            self.set_magnet(False)
            magnet_on = False
            time.sleep(MAGNET_RELEASE_SECONDS)
            self.move_to_point(destination_approach, move_time)
            if return_to_park:
                self.move_to_point("park", move_time)
        except BaseException:
            if magnet_on:
                self.set_magnet(False)
            raise
        self.emit("Transfer complete: {} -> {}.".format(source, destination))

    def teach_points(self, targets, prompt=input):
        """Record grab poses, shared approaches, and park in one torque-off session."""
        poses_to_teach = {}
        for target in (target.lower() for target in targets):
            if target == "approaches":
                poses_to_teach.update(dict.fromkeys(APPROACH_POINTS, "shared approach"))
            elif target in APPROACH_POINTS:
                poses_to_teach[target] = "shared approach"
            elif target == "park":
                poses_to_teach[target] = "clear of the board and all markers"
            else:
                poses_to_teach[self.board_coordinate(target) + "g"] = "grab/place"
        if prompt("Support the arm, then type OFF to release torque: ").strip() != "OFF":
            return
        self.set_magnet(False)
        self.set_torque(False)
        try:
            for name, position in poses_to_teach.items():
                if name in self.store.points and prompt(f"Overwrite {name}? [y/N] ").strip().lower() != "y":
                    continue
                while True:
                    action = prompt(f"Position {name} ({position}) then Enter, or q to stop: ").strip().lower()
                    if action == "q":
                        return
                    pose = self.read_pose()
                    if all(value is not None for value in pose):
                        self.store.save(name, pose)
                        self.emit(f"Saved {name}.")
                        break
                    self.emit("Incomplete feedback. Hold position and retry.")
        finally:
            self.emit("Torque remains off. Support the arm before requesting torque on.")

    @staticmethod
    def board_coordinate(value):
        coordinate = str(value).lower()
        if not re.fullmatch(r"[a-i][0-9]", coordinate):
            raise TeachError("Board coordinate must be a letter a-i followed by a row 0-9.")
        return coordinate

    def set_magnet(self, enabled):
        if self.electromagnet is None:
            raise TeachError("Electromagnet GPIO is not initialized.")
        self.electromagnet.set(enabled)
        action = "ON" if enabled else "OFF"
        level = "HIGH" if enabled else "LOW"
        self.emit("Electromagnet command: {} (GPIO{} {}).".format(
            action, ELECTROMAGNET_GPIO, level))

    def set_torque(self, enabled):
        self.arm.Arm_serial_set_torque(1 if enabled else 0)
        state = "ON" if enabled else "OFF"
        self.emit("Bus-servo torque {} request sent to the extension board; "
                  "individual servo torque states are not verified.".format(state))

    def read_status(self, servos):
        for servo in servos:
            try:
                status = self.arm.Arm_ping_servo(servo)
            except Exception as exc:
                self.emit("ID {} status=UNAVAILABLE: {}".format(servo, exc))
                continue
            if status is None:
                self.emit("ID {} status=UNAVAILABLE".format(servo))
                continue
            if status == 0xDA:
                description = "board reports normal"
            elif status == 0:
                description = "no servo reply reported by board"
            else:
                faults = []
                for bit, label in (
                        (0x20, "overload/stall protection"),
                        (0x04, "temperature fault"),
                        (0x01, "voltage fault")):
                    if status & bit:
                        faults.append(label)
                other = status & ~0x25
                if other:
                    faults.append("other status bits 0x{:02X}".format(other))
                description = ", ".join(faults)
            self.emit("ID {} status=0x{:02X}: {}".format(
                servo, status, description))
        self.emit("Status does not read torque enable or confirm arrival.")

    def execute(self, command_line):
        try:
            args = shlex.split(command_line)
        except ValueError as exc:
            raise TeachError(str(exc))
        if not args:
            return True

        command = args[0].lower()
        if command in ("quit", "exit"):
            return False
        if command in ("help", "?"):
            self.emit(HELP)
        elif command == "magnet" and len(args) == 2:
            action = args[1].lower()
            if action not in ("on", "off"):
                raise TeachError("Use 'magnet on' or 'magnet off'.")
            enabled = action == "on"
            self.set_magnet(enabled)
        elif command == "torque" and len(args) == 2:
            action = args[1].lower()
            if action not in ("on", "off"):
                raise TeachError("Use 'torque on' or 'torque off'.")
            self.set_torque(action == "on")
        elif command == "read" and len(args) == 1:
            self.read_pose()
        elif command == "teach" and len(args) >= 2:
            self.teach_points(args[1:])
        elif command == "coverage" and len(args) == 1:
            complete = taught_squares(self.store.points)
            self.emit("Complete ({}/90): {}; park: {}".format(
                len(complete), " ".join(complete) or "-",
                "saved" if "park" in self.store.points else "missing"))
            for name in APPROACH_POINTS:
                self.emit(f"{name}: {'saved' if name in self.store.points else 'missing'}")
        elif command == "status" and len(args) in (1, 2):
            servos = (servo_id(args[1]),) if len(args) == 2 else JOINT_IDS
            self.read_status(servos)
        elif command == "jog" and len(args) in (3, 4):
            servo = servo_id(args[1])
            delta = number(args[2], "Delta")
            move_time = duration(args[3] if len(args) == 4 else None)
            current = self.read_one(servo)
            if current is None:
                raise TeachError("No feedback from ID {}; jog was not sent.".format(servo))
            self.send_one(servo, current + delta, move_time)
        elif command == "set" and len(args) in (3, 4):
            self.send_one(servo_id(args[1]), number(args[2], "Angle"),
                          duration(args[3] if len(args) == 4 else None))
        elif command == "pose" and len(args) in (6, 7):
            self.send_pose([number(value, "Angle") for value in args[1:6]],
                           duration(args[6] if len(args) == 7 else None))
        elif command == "save" and len(args) == 2:
            pose = self.read_pose()
            if any(value is None for value in pose):
                raise TeachError("Point not saved because one or more readings failed.")
            self.store.save(args[1], pose)
            self.emit("Saved {}.".format(point_name(args[1])))
        elif command in ("delete", "del") and len(args) == 2:
            name = point_name(args[1])
            self.store.delete(name)
            self.emit("Deleted {}.".format(name))
        elif command == "goto" and len(args) in (2, 3):
            name = point_name(args[1])
            if name not in self.store.points:
                raise TeachError("Point {} has not been saved.".format(name))
            self.send_pose(self.store.points[name],
                           duration(args[2] if len(args) == 3 else None))
        elif command == "goto2" and len(args) in (3, 4):
            move_time = duration(args[3] if len(args) == 4 else None)
            if move_time == 0:
                raise TeachError("goto2 needs a nonzero duration so it can wait for each move.")
            self.transfer(args[1], args[2], move_time,
                          return_to_park="park" in self.store.points)
        elif command == "points" and len(args) == 1:
            if not self.store.points:
                self.emit("No saved points.")
            for name, pose in sorted(self.store.points.items()):
                self.emit("{}: {}".format(name, " ".join(
                    "{:.0f}".format(value) for value in pose)))
        else:
            raise TeachError("Unknown command or wrong number of arguments. Type 'help'.")
        return True


HELP = """Commands (angles are Arm_Lib logical degrees):
  magnet on / magnet off       Switch electromagnet (BCM GPIO21)
  torque off / torque on       Request torque off/on for all bus servos
  read                         Read IDs 1..5
  teach approaches             Record four shared approach poses
  teach SQUARE [SQUARE ...]    Record grab/place poses; accepts approaches and park
  coverage                     Show ready squares, shared approaches and park
  status [ID]                  Query board-reported servo status (all or one)
  jog ID DELTA [MS]             Relative move; no angle-step cap
  set ID ANGLE [MS]             Set one joint to an absolute angle
  pose J1 J2 J3 J4 J5 [MS]       Set all five joints in ID order
  save NAME                     Save the current five-angle pose
  delete NAME                   Delete one saved point (alias: del)
  goto NAME [MS]                 Replay a saved pose
  goto2 FROM TO [MS]             Move one piece, e.g. goto2 c2 g5
  points                        List saved poses
  help                          Show commands
  quit                          Exit

Default move time is 1000 ms. Use 0 for the board's fastest setting.
Torque requests affect the whole bus-servo group. Torque off may let the arm
drop. Status reports communication/faults, not actual torque enable or arrival.
The driver extends the vendor angle mapping and clamps only the encoded
position to the extension-board command range 96..4000. Read feedback is
displayed without angle clamping. goto2 uses the quadrant approach and saved
'g' (grab/place) poses for each coordinate, switches the magnet, and waits
for every timed move to finish."""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Five-servo arm teaching")
    parser.add_argument("--state", default=None,
                        help="point file path (default: teach_points.json beside this script)")
    options = parser.parse_args(argv)
    state_path = options.state or Path(__file__).with_name("teach_points.json")

    try:
        from Arm_Lib import Arm_Device
        arm = Arm_Device()
        store = PointStore(state_path)
        electromagnet = Electromagnet()
    except Exception as exc:
        print("Cannot start arm teaching: {}".format(exc), file=sys.stderr)
        return 1

    controller = TeachController(arm, store, electromagnet=electromagnet)
    print("Five-servo teaching ready. IDs 1..5 only; no motion sent at startup.")
    print("Electromagnet: BCM GPIO21 (physical pin 40), initialized LOW.")
    print("Type 'help' for commands.")
    try:
        while True:
            try:
                line = input("teach> ")
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                break
            try:
                if not controller.execute(line):
                    break
            except TeachError as exc:
                print("ERROR: {}".format(exc))
            except Exception as exc:
                print("ERROR: {}".format(exc))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        electromagnet.close()
    return 0


if __name__ == "__main__":
    try:
        with arm_lock():
            sys.exit(main())
    except TeachError as exc:
        sys.exit(str(exc))
