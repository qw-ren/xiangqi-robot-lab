# Servo communication and angle conversion

This note records the interfaces used by the teaching program. It distinguishes logical degrees in Arm_Lib from the integer position code sent through the extension board.

## Communication path

The Raspberry Pi uses SMBus/I2C bus 1 to address the extension board at `0x15`. The STM32/extension-board firmware is already installed and is not part of this project.

For a single servo, Arm_Lib writes register `0x10 + ID` with four bytes:

```text
position high, position low, duration high, duration low
```

Duration is an unsigned 16-bit millisecond value. The board protocol says `0` selects its fastest movement setting. Reads use register `0x30 + ID`: write `0x00` to request a sample, wait 3 ms, then read a 16-bit word. SMBus returns the word with the byte order reversed from the board's high-byte-first representation, so the driver swaps the two bytes before converting it.

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

- `None` is retained for an empty/zero response or a response outside the servo protocol's 16-bit position range. Ordinary extrapolated positions are converted to angles.
- I2C exceptions propagate to the caller rather than being converted into an apparent successful write.
- No feedback is interpreted as proof that a command physically completed. The current teaching interface reports the requested target and lets `read` inspect the result.
- The PC/APP protocol's `000..180` field is not used by this local Raspberry Pi program.
- This document describes the vendor protocol material supplied during development and the Python implementation. The original protocol files are not included in this repository, and this note does not infer any additional behavior from the unavailable STM32 source.

## Protocol references used during development

- `extension-board-i2c.xlsx`: extension-board register meanings, including `0x11..0x16`, `0x1D..0x1E`, and feedback registers `0x31..0x36`.
- `servo-bus-v4.01.pdf`: servo target/current position and limit registers.
- `pc-app-link.xlsx`: PC/APP link format; not used by this tool.

The Python implementation is in `Arm_Lib/Arm_Lib.py`; the interactive commands and GPIO magnet control are in `teach.py`.
