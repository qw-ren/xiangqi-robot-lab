# Servo communication and angle conversion

This note records the interfaces used by the teaching program. It distinguishes logical degrees in Arm_Lib from the integer position code sent through the extension board.

## Communication path

The Raspberry Pi uses SMBus/I2C bus 1 to address the extension board at `0x15`. The STM32/extension-board firmware is already installed and is not part of this project.

For a single servo, Arm_Lib writes register `0x10 + ID` with four bytes:

```text
position high, position low, duration high, duration low
```

Duration is an unsigned 16-bit millisecond value. The board protocol says `0` selects its fastest movement setting. Reads use register `0x30 + ID`: write `0x00` to request a sample, wait 3 ms, then read a 16-bit word. The supplied extension-board document instead specifies writing `0x01` and waiting 5 ms. This implementation retains the vendor Python behavior (`0x00` / 3 ms); the installed firmware's handling of that discrepancy has not been established. SMBus returns the word with the byte order reversed from the board's high-byte-first representation, so the driver swaps the two bytes before converting it.

The board-wide bus-servo torque switch uses register `0x1A`: write `0x00` to disable torque and allow manual repositioning, or `0x01` to enable torque. This is the vendor `Arm_serial_set_torque` behavior; it affects the bus-servo group, not one selected ID. The teaching commands are `torque off` and `torque on`. No torque-state readback is provided here.

For servo status, write the requested ID to extension-board register `0x38`, wait 5 ms, then read one byte from that register. The supplied board document defines `0xDA` as a normal reply and `0x00` as no reply; other values are fault status. Its status-bit diagram specifies bit 5 (`0x20`) for overload/stall protection, bit 2 (`0x04`) for temperature, and bit 0 (`0x01`) for voltage. The teaching command is `status [ID]`. This does not read the torque-enable state or confirm that a motion completed. The vendor library used a 3 ms query delay; this diagnostic waits 5 ms to allow more response time.

These are extension-board I2C register addresses. The servo UART protocol separately places its torque switch at servo register `0x28` and current position at servo register `0x38`; the board does not document an arbitrary-servo-register read/write bridge. Sending those UART register addresses directly to the board's I2C interface would not access the same function.

An I2C write returning without an exception does not acknowledge execution by each downstream servo. The Python position-read function does not explicitly write torque, but the installed extension-board firmware is unavailable, so this code cannot establish whether a read request has downstream side effects.

Only IDs 1..5 are exposed. The driver rejects ID 6 and does not call the vendor six-servo broadcast methods.

## Vendor logical-angle maps

The vendor maps angles to servo position codes as follows. The same straight-line formula is extended beyond the named angle marks.

| ID | Vendor logical angle marks | Position-code marks | Direction |
| --- | --- | --- | --- |
| 1 | 0..180 deg | 900..3100 | increasing |
| 2 | 0..180 deg | 3100..900 | reversed |
| 3 | 0..180 deg | 3100..900 | reversed |
| 4 | 0..180 deg | 3100..900 | reversed |
| 5 | 0..270 deg | 380..3700 | increasing |

For IDs 1..4, one nominal degree corresponds to `2200 / 180` position units. IDs 2..4 reverse the input angle before applying that scale. ID 5 uses `3320 / 270` position units per degree. The read conversion is the inverse mapping; returned values are integer degrees, matching the original library's integer result style. Angles beyond the original marks therefore produce negative or greater-than-nominal readings instead of `None`.

The driver clamps the encoded write position to `96..4000`, as specified by the extension-board I2C document. There is no software angle limit for IDs 1..5 and no maximum jog delta. Read feedback remains unclamped so the program can show the reported angle. The servo protocol separately describes target/current position codes as `0..4095` and provides min/max position registers; those codes are not degrees. The extension-board write range and the servo protocol's register range are distinct protocol layers.

## Behavior and limits of this record

- `None` is retained for an empty/zero response or a response outside the servo position-code range `1..4095` (carried in a 16-bit field). Ordinary extrapolated positions are converted to angles.
- I2C exceptions propagate to the caller rather than being converted into an apparent successful write.
- No feedback is interpreted as proof that a command physically completed. The current teaching interface reports the requested target and lets `read` inspect the result.
- The PC/APP protocol's `000..180` field is not used by this local Raspberry Pi program.
- This document describes the supplied protocol files and the Python implementation. It does not infer any additional behavior from the unavailable STM32 source.

## Protocol references used during development

- `extension-board-i2c.xlsx`: extension-board registers, including motion, torque, status and position feedback.
- `servo-bus-v4.01.pdf`: servo target/current position and limit registers.
- `pc-app-link.xlsx`: PC/APP link format; not used by this tool.

The original protocol files are retained in the owner's local reference archive and are not included in this module. The Python implementation is in `Arm_Lib/Arm_Lib.py`; interactive commands and GPIO magnet control are in `teach.py`.
