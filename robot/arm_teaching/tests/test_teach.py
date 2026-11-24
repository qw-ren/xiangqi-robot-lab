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
    quadrant_approach,
    taught_squares,
)


class FakeArm:
    def __init__(self, values=None):
        self.values = list(values or [90, 90, 90, 90, 90])
        self.writes = []
        self.torque_states = []
        self.status_values = [0xDA] * 5
        self.status_requests = []

    def Arm_serial_servo_read(self, servo_id):
        return self.values[servo_id - 1]

    def Arm_serial_servo_write(self, servo_id, angle, move_time):
        self.writes.append((servo_id, angle, move_time))

    def Arm_serial_set_torque(self, onoff):
        self.torque_states.append(onoff)

    def Arm_ping_servo(self, servo_id):
        self.status_requests.append(servo_id)
        return self.status_values[servo_id - 1]


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

    def test_torque_commands_disable_and_enable_all_bus_servos(self):
        arm = FakeArm()
        controller = TeachController(
            arm,
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            emit=lambda _: None,
        )
        controller.execute("torque off")
        controller.execute("torque on")
        self.assertEqual(arm.torque_states, [0, 1])

    def test_invalid_torque_command_is_rejected(self):
        arm = FakeArm()
        controller = TeachController(
            arm,
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            emit=lambda _: None,
        )
        with self.assertRaises(TeachError):
            controller.execute("torque toggle")
        self.assertEqual(arm.torque_states, [])

    def test_status_reports_normal_no_reply_and_combined_faults(self):
        arm = FakeArm()
        arm.status_values = [0xDA, 0x00, 0x25, 0x40, None]
        output = []
        controller = TeachController(
            arm,
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            emit=output.append,
        )
        controller.execute("status")
        self.assertEqual(arm.status_requests, [1, 2, 3, 4, 5])
        self.assertIn("0xDA: board reports normal", output[0])
        self.assertIn("0x00: no servo reply", output[1])
        self.assertIn("0x25: overload/stall protection, temperature fault, voltage fault", output[2])
        self.assertIn("other status bits 0x40", output[3])
        self.assertIn("UNAVAILABLE", output[4])
        self.assertIn("does not read torque enable", output[-1])
        self.assertEqual(arm.writes, [])
        self.assertEqual(arm.torque_states, [])

    def test_status_can_query_one_joint(self):
        arm = FakeArm()
        controller = TeachController(
            arm,
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            emit=lambda _: None,
        )
        controller.execute("status 3")
        self.assertEqual(arm.status_requests, [3])
        with self.assertRaises(TeachError):
            controller.execute("status 6")
        self.assertEqual(arm.status_requests, [3])

    def test_status_continues_after_one_joint_query_fails(self):
        class FailingStatusArm(FakeArm):
            def Arm_ping_servo(self, servo_id):
                self.status_requests.append(servo_id)
                if servo_id == 2:
                    raise OSError("simulated status query failure")
                return self.status_values[servo_id - 1]

        arm = FailingStatusArm()
        output = []
        controller = TeachController(
            arm,
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            emit=output.append,
        )
        controller.execute("status")
        self.assertEqual(arm.status_requests, [1, 2, 3, 4, 5])
        self.assertIn("ID 2 status=UNAVAILABLE", output[1])
        self.assertIn("ID 5 status=0xDA", output[4])

    def test_torque_report_does_not_claim_hardware_confirmation(self):
        output = []
        controller = TeachController(
            FakeArm(),
            PointStore(Path(tempfile.gettempdir()) / "unused-points.json"),
            emit=output.append,
        )
        controller.execute("torque on")
        self.assertIn("ON request sent", output[-1])
        self.assertIn("not verified", output[-1])

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
        self.assertEqual(arm.writes, [(5, 281.0, 1000), (5, 281.0, 1000)])

    def test_jog_uses_current_logical_angle_without_a_delta_cap(self):
        arm = FakeArm([10, 0, 0, 0, 0])
        controller = TeachController(arm, PointStore(Path(tempfile.gettempdir()) / "unused-points.json"), emit=lambda _: None)
        controller.execute("jog 1 500")
        self.assertEqual(arm.writes, [(1, 510.0, 1000)])

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

    def test_delete_removes_point_from_memory_and_persisted_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "points.json"
            store = PointStore(path)
            store.save("c2a", [1, 2, 3, 4, 5])
            store.save("c2g", [6, 7, 8, 9, 10])
            controller = TeachController(FakeArm(), store, emit=lambda _: None)

            controller.execute("delete c2a")
            reopened = PointStore(path)

        self.assertNotIn("c2a", store.points)
        self.assertEqual(reopened.points, {"c2g": [6, 7, 8, 9, 10]})

    def test_del_alias_removes_named_point(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = PointStore(Path(temp_dir) / "points.json")
            store.save("g5g", [1, 2, 3, 4, 5])
            controller = TeachController(FakeArm(), store, emit=lambda _: None)
            controller.execute("del g5g")
        self.assertEqual(store.points, {})

    def test_delete_unknown_point_does_not_change_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "points.json"
            store = PointStore(path)
            store.save("c2a", [1, 2, 3, 4, 5])
            original_file = path.read_text(encoding="utf-8")
            controller = TeachController(FakeArm(), store, emit=lambda _: None)

            with self.assertRaises(TeachError):
                controller.execute("delete missing")

            self.assertEqual(store.points, {"c2a": [1, 2, 3, 4, 5]})
            self.assertEqual(path.read_text(encoding="utf-8"), original_file)

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

    def test_goto2_defaults_to_one_second_and_waits_between_stages(self):
        controller, arm, gpio = self.make_transfer_controller()
        with patch("teach.time.sleep") as sleep:
            controller.execute("goto2 c2 g5")
        self.assertEqual(len(arm.writes), 30)
        self.assertTrue(all(item[2] == 1000 for item in arm.writes))
        waits = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(waits.count(1.15), 6)
        self.assertEqual(waits.count(1.0), 1)
        self.assertEqual(waits.count(0.25), 1)

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

    def test_integrated_transfer_requires_park_before_any_write(self):
        controller, arm, gpio = self.make_transfer_controller()
        with self.assertRaisesRegex(TeachError, "park"):
            controller.transfer("c2", "g5", 1000, return_to_park=True)
        self.assertEqual(arm.writes, [])
        self.assertEqual(len(gpio.calls), 2)

    def test_transfer_to_capture_slot_releases_piece_and_parks(self):
        controller, arm, gpio = self.make_transfer_controller()
        controller.store.points.update(capturea=[50] * 5, captureg=[60] * 5, park=[70] * 5)
        with patch("teach.time.sleep"):
            controller.execute("goto2 c2 capture")
        self.assertEqual([group[1] for group in arm.writes[::5]],
                         [10, 20, 10, 50, 60, 50, 70])
        self.assertEqual(gpio.calls[-1], ("output", 21, FakeGPIO.LOW))

    def test_capture_transfer_requires_both_slot_poses_before_any_write(self):
        controller, arm, _ = self.make_transfer_controller()
        controller.store.points["capturea"] = [50] * 5
        with self.assertRaisesRegex(TeachError, "captureg"):
            controller.execute("goto2 c2 capture")
        self.assertEqual(arm.writes, [])

    def test_transfer_ends_at_registered_park_with_magnet_off(self):
        controller, arm, gpio = self.make_transfer_controller()
        controller.store.points["park"] = [50] * 5
        with patch("teach.time.sleep"):
            controller.execute("goto2 c2 g5")
        self.assertEqual([group[1] for group in arm.writes[::5]],
                         [10, 20, 10, 30, 40, 30, 50])
        self.assertEqual(gpio.calls[-1], ("output", 21, FakeGPIO.LOW))

    def test_batch_teaching_saves_grab_poses_and_leaves_torque_off(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PointStore(Path(directory) / "points.json")
            arm = FakeArm()
            controller = TeachController(arm, store, Electromagnet(gpio=FakeGPIO()),
                                         emit=lambda _: None)
            answers = iter(["OFF", "", "q"])
            with patch.object(controller, "read_pose") as read:
                read.return_value = [1, 2, 3, 4, 5]
                controller.teach_points(["c2", "g5"], prompt=lambda _: next(answers))
            self.assertEqual(set(PointStore(store.path).points), {"c2g"})
            self.assertEqual(arm.torque_states, [0])

    def test_quadrants_include_center_file_on_left(self):
        self.assertEqual(quadrant_approach("e4"), "approach_red_left")
        self.assertEqual(quadrant_approach("f4"), "approach_red_right")
        self.assertEqual(quadrant_approach("e5"), "approach_black_left")
        self.assertEqual(quadrant_approach("f5"), "approach_black_right")

    def test_shared_approaches_enable_reused_grab_points_and_require_target_region(self):
        controller, arm, _ = self.make_transfer_controller()
        points = controller.store.points
        del points["c2a"], points["g5a"]
        points["approach_red_left"] = [50] * 5
        self.assertEqual(taught_squares(points), ["c2"])
        with self.assertRaisesRegex(TeachError, "approach_black_right"):
            controller.execute("goto2 c2 g5")
        self.assertEqual(arm.writes, [])
        points["approach_black_right"] = [60] * 5
        with patch("teach.time.sleep"):
            controller.execute("goto2 c2 g5")
        self.assertEqual([group[1] for group in arm.writes[::5]],
                         [50, 20, 50, 60, 40, 60])

    def test_transfer_within_one_quadrant_reuses_approach_without_duplicate_motion(self):
        controller, arm, _ = self.make_transfer_controller()
        del controller.store.points["c2a"]
        controller.store.points.update(approach_red_left=[50] * 5, e4g=[70] * 5)
        with patch("teach.time.sleep"):
            controller.execute("goto2 c2 e4")
        self.assertEqual([group[1] for group in arm.writes[::5]], [50, 20, 50, 70, 50])

    def test_numbered_capture_uses_individual_approach_and_parks(self):
        controller, arm, _ = self.make_transfer_controller()
        controller.store.points.update(approach_red_left=[50] * 5, capture16a=[60] * 5,
                                       capture16g=[70] * 5, park=[80] * 5)
        with patch("teach.time.sleep"):
            controller.execute("goto2 c2 capture16")
        self.assertEqual([group[1] for group in arm.writes[::5]],
                         [10, 20, 10, 60, 70, 60, 80])


if __name__ == "__main__":
    unittest.main()
