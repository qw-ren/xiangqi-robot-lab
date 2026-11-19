#!/usr/bin/env python3
"""Minimal Raspberry Pi I2C driver for the five active bus servos."""

import math
import time


BOARD_ADDRESS = 0x15
POSITION_MIN = 96
POSITION_MAX = 4000
SERVO_POSITION_MAX = 4095
ACTIVE_IDS = (1, 2, 3, 4, 5)


def _servo_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in ACTIVE_IDS:
        raise ValueError("servo ID must be 1..5")
    return value


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError("{} must be finite".format(name))
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("{} must be finite".format(name))
    if not math.isfinite(result):
        raise ValueError("{} must be finite".format(name))
    return result


def angle_to_position(servo_id, angle):
    """Convert Arm_Lib logical degrees to the extension-board position code."""
    servo_id = _servo_id(servo_id)
    angle = _finite(angle, "angle")

    if servo_id in (2, 3, 4):
        angle = 180.0 - angle
        position = 900.0 + (3100.0 - 900.0) * angle / 180.0
    elif servo_id == 5:
        position = 380.0 + (3700.0 - 380.0) * angle / 270.0
    else:
        position = 900.0 + (3100.0 - 900.0) * angle / 180.0

    return max(POSITION_MIN, min(POSITION_MAX, int(position)))


def position_to_angle(servo_id, position):
    """Convert a reported servo position to extrapolated logical degrees."""
    servo_id = _servo_id(servo_id)
    if isinstance(position, bool) or not isinstance(position, int):
        raise ValueError("position must be an integer code")
    if not 1 <= position <= SERVO_POSITION_MAX:
        return None

    if servo_id == 5:
        return int(270.0 * (position - 380.0) / (3700.0 - 380.0))

    angle = int(180.0 * (position - 900.0) / (3100.0 - 900.0))
    return 180 - angle if servo_id in (2, 3, 4) else angle


class Arm_Device:
    """I2C access to IDs 1..5; angle conversion extends beyond vendor marks."""

    def __init__(self, bus=None):
        if bus is None:
            try:
                import smbus
            except ImportError as exc:
                raise RuntimeError("smbus is required on the Raspberry Pi: {}".format(exc))
            bus = smbus.SMBus(1)
        self.addr = BOARD_ADDRESS
        self.bus = bus

    def Arm_serial_servo_write(self, servo_id, angle, duration_ms):
        servo_id = _servo_id(servo_id)
        position = angle_to_position(servo_id, angle)
        duration = _finite(duration_ms, "duration")
        if not duration.is_integer() or not 0 <= duration <= 65535:
            raise ValueError("duration must be an integer from 0 to 65535 ms")

        self.bus.write_i2c_block_data(
            self.addr,
            0x10 + servo_id,
            [position >> 8, position & 0xFF,
             int(duration) >> 8, int(duration) & 0xFF],
        )

    def Arm_serial_servo_read(self, servo_id):
        servo_id = _servo_id(servo_id)
        register = 0x30 + servo_id
        self.bus.write_byte_data(self.addr, register, 0)
        time.sleep(0.003)
        word = self.bus.read_word_data(self.addr, register)
        position = ((word >> 8) & 0xFF) | ((word << 8) & 0xFF00)
        return position_to_angle(servo_id, position)
