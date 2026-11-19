import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Arm_Lib.Arm_Lib import (  # noqa: E402
    Arm_Device,
    angle_to_position,
    position_to_angle,
)


class FakeBus:
    def __init__(self, position=900, status=0xDA):
        self.calls = []
        self.position = position
        self.status = status

    def write_i2c_block_data(self, address, register, data):
        self.calls.append(("block", address, register, data))

    def write_byte_data(self, address, register, value):
        self.calls.append(("byte", address, register, value))

    def read_word_data(self, address, register):
        value = self.position
        return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

    def read_byte_data(self, address, register):
        self.calls.append(("read_byte", address, register))
        return self.status


class AngleMappingTests(unittest.TestCase):
    def test_vendor_logical_marks_are_preserved(self):
        self.assertEqual(angle_to_position(1, 0), 900)
        self.assertEqual(angle_to_position(1, 180), 3100)
        self.assertEqual(angle_to_position(2, 0), 3100)
        self.assertEqual(angle_to_position(2, 180), 900)
        self.assertEqual(angle_to_position(4, 90), 2000)
        self.assertEqual(angle_to_position(5, 0), 380)
        self.assertEqual(angle_to_position(5, 270), 3700)

    def test_angle_mapping_extends_and_clamps_to_board_range(self):
        self.assertEqual(angle_to_position(1, -100), 96)
        self.assertEqual(angle_to_position(1, 300), 4000)
        self.assertEqual(angle_to_position(2, -100), 4000)
        self.assertEqual(angle_to_position(5, 280), 3822)

    def test_servo_five_command_angles_are_extrapolated_without_a_limit(self):
        self.assertEqual(angle_to_position(5, -1), 367)
        self.assertEqual(angle_to_position(5, 281), 3835)

    def test_read_mapping_reports_extrapolated_angles(self):
        self.assertEqual(position_to_angle(1, 96), -65)
        self.assertEqual(position_to_angle(1, 4000), 253)
        self.assertEqual(position_to_angle(2, 96), 245)
        self.assertEqual(position_to_angle(2, 4000), -73)
        self.assertEqual(position_to_angle(5, 96), -23)
        self.assertEqual(position_to_angle(5, 4000), 294)
        self.assertEqual(position_to_angle(5, 3822), 279)
        self.assertIsNone(position_to_angle(1, 0))
        self.assertIsNone(position_to_angle(1, 4096))

    def test_only_active_ids_are_accepted(self):
        for invalid_id in (0, 6, -1):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(ValueError):
                    angle_to_position(invalid_id, 90)

    def test_write_encodes_position_and_duration_big_endian(self):
        bus = FakeBus()
        arm = Arm_Device(bus=bus)
        arm.Arm_serial_servo_write(1, 180, 500)
        self.assertEqual(bus.calls, [("block", 0x15, 0x11, [0x0C, 0x1C, 0x01, 0xF4])])

    def test_write_clamps_only_the_encoded_board_position(self):
        bus = FakeBus()
        arm = Arm_Device(bus=bus)
        arm.Arm_serial_servo_write(1, -100, 500)
        arm.Arm_serial_servo_write(1, 300, 500)
        self.assertEqual(bus.calls[0][3][:2], [0x00, 0x60])
        self.assertEqual(bus.calls[1][3][:2], [0x0F, 0xA0])

    def test_read_requests_angle_and_keeps_extrapolated_result(self):
        bus = FakeBus(position=4000)
        arm = Arm_Device(bus=bus)
        self.assertEqual(arm.Arm_serial_servo_read(1), 253)
        self.assertEqual(bus.calls[0], ("byte", 0x15, 0x31, 0))

    def test_torque_switch_writes_board_register(self):
        bus = FakeBus()
        arm = Arm_Device(bus=bus)
        arm.Arm_serial_set_torque(0)
        arm.Arm_serial_set_torque(1)
        self.assertEqual(bus.calls, [
            ("byte", 0x15, 0x1A, 0),
            ("byte", 0x15, 0x1A, 1),
        ])

    def test_torque_switch_rejects_invalid_state(self):
        arm = Arm_Device(bus=FakeBus())
        for state in (-1, 2, True, 0.0, "1"):
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    arm.Arm_serial_set_torque(state)

    def test_status_uses_board_status_register_and_waits_for_reply(self):
        bus = FakeBus()
        arm = Arm_Device(bus=bus)
        with patch("Arm_Lib.Arm_Lib.time.sleep") as sleep:
            self.assertEqual(arm.Arm_ping_servo(3), 0xDA)
        self.assertEqual(bus.calls, [
            ("byte", 0x15, 0x38, 3),
            ("read_byte", 0x15, 0x38),
        ])
        sleep.assert_called_once_with(0.005)

    def test_status_preserves_no_reply_and_combined_faults(self):
        for value in (0x00, 0x20, 0x25, 0x40):
            with self.subTest(status=value):
                arm = Arm_Device(bus=FakeBus(status=value))
                with patch("Arm_Lib.Arm_Lib.time.sleep"):
                    self.assertEqual(arm.Arm_ping_servo(2), value)

    def test_status_rejects_inactive_id_before_using_bus(self):
        bus = FakeBus()
        arm = Arm_Device(bus=bus)
        with self.assertRaises(ValueError):
            arm.Arm_ping_servo(6)
        self.assertEqual(bus.calls, [])

    def test_status_propagates_i2c_errors(self):
        arm = Arm_Device(bus=FakeBus())
        with patch.object(arm.bus, "read_byte_data", side_effect=OSError("bus error")):
            with patch("Arm_Lib.Arm_Lib.time.sleep"):
                with self.assertRaises(OSError):
                    arm.Arm_ping_servo(3)

    def test_single_servo_api_rejects_id_six(self):
        bus = FakeBus()
        arm = Arm_Device(bus=bus)
        with self.assertRaises(ValueError):
            arm.Arm_serial_servo_write(6, 90, 500)
        with self.assertRaises(ValueError):
            arm.Arm_serial_servo_read(6)
        self.assertEqual(bus.calls, [])


if __name__ == "__main__":
    unittest.main()
