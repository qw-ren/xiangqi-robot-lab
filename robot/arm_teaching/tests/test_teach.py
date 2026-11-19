import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from teach import (  # noqa: E402
    Electromagnet,
    PointStore,
    TeachController,
    TeachError,
)


class FakeArm:
    def __init__(self, values=None):
        self.values = list(values or [90, 90, 90, 90, 90])
        self.writes = []

    def Arm_serial_servo_read(self, servo_id):
        return self.values[servo_id - 1]

    def Arm_serial_servo_write(self, servo_id, angle, move_time):
        self.writes.append((servo_id, angle, move_time))


class FakeGPIO:
    BCM = "BCM"
    OUT = "OUT"
    LOW = 0
    HIGH = 1

    def __init__(self):
        self.calls = []

    def setmode(self, mode):
        self.calls.append(("setmode", mode))

    def setup(self, pin, mode, initial=None):
        self.calls.append(("setup", pin, mode, initial))

    def output(self, pin, value):
        self.calls.append(("output", pin, value))

    def cleanup(self, pin):
        self.calls.append(("cleanup", pin))


class TeachControllerTests(unittest.TestCase):
    def test_electromagnet_uses_bcm_21_and_starts_low(self):
        gpio = FakeGPIO()
        magnet = Electromagnet(gpio=gpio)
        self.assertEqual(gpio.calls, [
            ("setmode", FakeGPIO.BCM),
            ("setup", 21, FakeGPIO.OUT, FakeGPIO.LOW),
        ])
        self.assertFalse(magnet.is_on)

    def test_magnet_commands_drive_high_and_low(self):
        gpio = FakeGPIO()
        magnet = Electromagnet(gpio=gpio)
        controller = TeachController(
            FakeArm(),
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            electromagnet=magnet,
            emit=lambda _: None,
        )
        controller.execute("magnet on")
        controller.execute("magnet off")
        self.assertEqual(gpio.calls[-2:], [
            ("output", 21, FakeGPIO.HIGH),
            ("output", 21, FakeGPIO.LOW),
        ])
        self.assertFalse(magnet.is_on)

    def test_electromagnet_close_sets_low(self):
        gpio = FakeGPIO()
        magnet = Electromagnet(gpio=gpio)
        magnet.set(True)
        magnet.close()
        self.assertEqual(gpio.calls[-1], ("output", 21, FakeGPIO.LOW))

    def test_invalid_magnet_command_does_not_change_output(self):
        gpio = FakeGPIO()
        magnet = Electromagnet(gpio=gpio)
        controller = TeachController(
            FakeArm(),
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            electromagnet=magnet,
            emit=lambda _: None,
        )
        with self.assertRaises(TeachError):
            controller.execute("magnet toggle")
        self.assertEqual(len(gpio.calls), 2)

    def test_set_accepts_extended_angles_without_a_step_limit(self):
        arm = FakeArm()
        controller = TeachController(arm, PointStore(Path(tempfile.gettempdir()) / "unused-points.json"), emit=lambda _: None)
        controller.execute("set 1 500 700")
        self.assertEqual(arm.writes, [(1, 500.0, 700)])

    def test_servo_five_accepts_extended_angles(self):
        arm = FakeArm([90, 90, 90, 90, 279])
        controller = TeachController(arm, PointStore(Path(tempfile.gettempdir()) / "unused-points.json"), emit=lambda _: None)
        controller.execute("set 5 281")
        controller.execute("jog 5 2")
        self.assertEqual(arm.writes, [(5, 281.0, 500), (5, 281.0, 500)])

    def test_jog_uses_current_logical_angle_without_a_delta_cap(self):
        arm = FakeArm([10, 0, 0, 0, 0])
        controller = TeachController(arm, PointStore(Path(tempfile.gettempdir()) / "unused-points.json"), emit=lambda _: None)
        controller.execute("jog 1 500")
        self.assertEqual(arm.writes, [(1, 510.0, 500)])

    def test_pose_sends_only_ids_one_through_five_in_order(self):
        arm = FakeArm()
        controller = TeachController(arm, PointStore(Path(tempfile.gettempdir()) / "unused-points.json"), emit=lambda _: None)
        with patch("teach.time.sleep") as sleep:
            controller.execute("pose 1 2 3 4 5 250")
        self.assertEqual(arm.writes, [(i, float(i), 250) for i in range(1, 6)])
        self.assertEqual(sleep.call_count, 4)

    def test_id_six_is_rejected_before_any_write(self):
        arm = FakeArm()
        controller = TeachController(arm, PointStore(Path(tempfile.gettempdir()) / "unused-points.json"), emit=lambda _: None)
        with self.assertRaises(TeachError):
            controller.execute("set 6 90")
        self.assertEqual(arm.writes, [])

    def test_saved_points_keep_unbounded_logical_angles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "points.json"
            store = PointStore(path)
            store.save("extended", [-50, 220, 90, 180, 310])
            reopened = PointStore(path)
        self.assertEqual(reopened.points["extended"], [-50, 220, 90, 180, 310])

    def test_failed_feedback_does_not_save_a_partial_pose(self):
        arm = FakeArm()
        arm.Arm_serial_servo_read = lambda servo_id: None if servo_id == 3 else 90
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PointStore(Path(temp_dir) / "points.json")
            controller = TeachController(arm, store, emit=lambda _: None)
            with self.assertRaises(TeachError):
                controller.execute("save incomplete")
            self.assertEqual(store.points, {})

    def test_save_accepts_extended_servo_five_feedback(self):
        arm = FakeArm([90, 90, 90, 90, 281])
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PointStore(Path(temp_dir) / "points.json")
            controller = TeachController(arm, store, emit=lambda _: None)
            controller.execute("save extended_j5")
            self.assertEqual(store.points["extended_j5"], [90, 90, 90, 90, 281])

    def make_transfer_controller(self, arm=None, gpio=None):
        store = PointStore(Path(tempfile.gettempdir()) / "unused-points.json")
        store.points = {
            "c2a": [10, 10, 10, 10, 10],
            "c2g": [20, 20, 20, 20, 20],
            "g5a": [30, 30, 30, 30, 30],
            "g5g": [40, 40, 40, 40, 40],
        }
        gpio = gpio or FakeGPIO()
        magnet = Electromagnet(gpio=gpio)
        arm = arm or FakeArm()
        controller = TeachController(
            arm, store, electromagnet=magnet, emit=lambda _: None)
        return controller, arm, gpio

    def test_goto2_sends_the_verified_pose_and_magnet_sequence(self):
        controller, arm, gpio = self.make_transfer_controller()
        with patch("teach.time.sleep") as sleep:
            controller.execute("goto2 c2 g5 700")

        groups = [arm.writes[i:i + 5] for i in range(0, len(arm.writes), 5)]
        self.assertEqual([group[0][1] for group in groups], [10, 20, 10, 30, 40, 30])
        self.assertTrue(all(
            [item[0] for item in group] == [1, 2, 3, 4, 5]
            and all(item[2] == 700 for item in group)
            for group in groups))
        self.assertEqual(gpio.calls[-2:], [
            ("output", 21, FakeGPIO.HIGH),
            ("output", 21, FakeGPIO.LOW),
        ])
        self.assertEqual(sleep.call_count, 32)

    def test_goto2_checks_all_points_before_moving_or_switching_magnet(self):
        controller, arm, gpio = self.make_transfer_controller()
        del controller.store.points["g5g"]
        with self.assertRaises(TeachError):
            controller.execute("goto2 c2 g5")
        self.assertEqual(arm.writes, [])
        self.assertEqual(len(gpio.calls), 2)

    def test_goto2_rejects_invalid_board_coordinate(self):
        controller, arm, gpio = self.make_transfer_controller()
        with self.assertRaises(TeachError):
            controller.execute("goto2 z2 g5")
        self.assertEqual(arm.writes, [])
        self.assertEqual(len(gpio.calls), 2)

    def test_goto2_turns_magnet_off_if_motion_fails_after_pickup(self):
        class FailingArm(FakeArm):
            def Arm_serial_servo_write(self, servo_id, angle, move_time):
                if len(self.writes) == 10:
                    raise RuntimeError("simulated I2C failure")
                super().Arm_serial_servo_write(servo_id, angle, move_time)

        controller, arm, gpio = self.make_transfer_controller(arm=FailingArm())
        with patch("teach.time.sleep"):
            with self.assertRaises(RuntimeError):
                controller.execute("goto2 c2 g5")
        self.assertEqual(gpio.calls[-2:], [
            ("output", 21, FakeGPIO.HIGH),
            ("output", 21, FakeGPIO.LOW),
        ])

    def test_goto2_rejects_fastest_duration_because_completion_time_is_unknown(self):
        controller, arm, gpio = self.make_transfer_controller()
        with self.assertRaises(TeachError):
            controller.execute("goto2 c2 g5 0")
        self.assertEqual(arm.writes, [])
        self.assertEqual(len(gpio.calls), 2)


if __name__ == "__main__":
    unittest.main()
