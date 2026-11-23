# Raspberry Pi teaching program

## Deploy and start

This is a standalone Raspberry Pi tool. It talks directly to the Yahboom extension board over I2C and controls the electromagnet through GPIO; it is not connected to this repository's TCP/serial game-control path.

On Raspberry Pi OS, install the system Python bindings and enable I2C:

```sh
sudo apt update
sudo apt install python3-rpi.gpio python3-smbus
```

Copy `teach.py` and the whole `Arm_Lib/` directory to one directory on the Raspberry Pi. Both are required for the extended angle mapping unless the same driver changes have already been installed there. Run with system Python so it can import the OS-provided hardware modules:

```sh
python3 teach.py
```

Startup opens the I2C device but sends no servo movement. Point data is stored in `teach_points.json` beside the script.

## Commands

```text
magnet on
magnet off
torque off
torque on
goto2 c2 g5
goto2 c2 g5 700
read
status
status 3
jog 2 5
jog 5 -30 1000
set 1 220
pose 90 80 100 120 30 700
save A_UP
delete A_UP
goto A_UP
points
help
quit
```

`goto2 FROM TO [MS]` performs the verified piece-transfer sequence. For
`goto2 c2 g5`, the program first checks the approach and grab/place pose
at both ends. With a legacy point file the route is:

```text
c2a -> c2g -> magnet on -> c2a -> g5a -> g5g
    -> magnet off -> g5a
```

The suffix `a` means approach and `g` means grab/place. Every move waits for
the requested duration plus a short settling delay. The magnet also gets a
short settling delay after switching. `goto2` requires a nonzero move time so
the program can estimate when each timed movement is complete; the default is
1000 ms. If a movement fails or the macro is interrupted after pickup, it
commands the magnet OFF before returning control. That can release the piece
where it is, so inspect the arm and board before continuing.

Point names use lowercase board coordinates: columns `a` through `i`, rows
`0` through `9`, plus `a`/`g`. Save the four example poses with:

```text
teach approaches c2 g5
```

The existing manual commands remain available for calibration and
troubleshooting. When `[MS]` is omitted, `set`, `jog`, `pose`, `goto` and
every motion stage of `goto2` use 1000 ms. An explicit `[MS]` overrides it.
The macro waits an additional 150 ms after each set of five writes and
250 ms after each magnet switch. These are timed waits, not arrival feedback.
Approach-to-approach moves follow joint motion; this tool does not plan
Cartesian paths or intermediate clearance waypoints.

Use `delete NAME` to remove a point saved under the wrong name or with an
incorrect pose. `del NAME` is a shorter alias. The change is written to
`teach_points.json` immediately; deleting an unknown name reports an error and
leaves the point file unchanged. Saving the same name again still replaces
that point with the current five-servo readings.

The electromagnet control uses BCM GPIO21 (physical pin 40). `magnet on` drives it HIGH; `magnet off` drives it LOW. It initializes LOW and is set LOW again on normal exit or Ctrl+C, leaving the pin configured as an output at LOW. The displayed state is the command sent by software, not electrical feedback from the magnet. This requires `RPi.GPIO` on the Raspberry Pi.

`torque off` requests release of the holding torque of all bus servos so the arm can be repositioned by hand; `torque on` requests restoration. This is a board-wide bus-servo switch, not an individual-joint command, and a released arm or load may drop. A successful I2C write only means the request was sent to the extension board: the program cannot confirm each servo's torque state through this switch. It does not change electromagnet state. The program does not toggle servo torque automatically at startup or exit.

## Diagnosing a servo that does not move

`status` queries the board's servo-status register for IDs 1..5; `status 3` queries only ID 3. It prints the returned byte and any documented fault bits:

- `0xDA`: the extension board reports a normal response.
- `0x00`: the board reports no servo reply.
- Other values: fault bits `0x20` overload/stall protection, `0x04` temperature, and `0x01` voltage. These can combine; unknown bits are printed separately.

Status is a separate query from position feedback. It does not read the servo's torque-enable register or confirm arrival, and a normal response can come from a servo with torque disabled. If a query fails, the command reports the failure and continues with the other IDs. It sends no torque or position commands.

For a fault report, run `status` and `read` while the problem is present and retain both outputs. The `ID ... target` lines printed by `set` or `goto` record sent targets; they are not servo acknowledgments. Manual `goto` returns after sending the five commands and does not wait for motion completion, so wait at least the requested movement duration before checking the result. Persistent non-movement across repeated checks needs further diagnosis even if the status is normal.

The Python position-read method sends a sample request and does not explicitly send a torque command. The unavailable extension-board firmware controls the downstream bus behavior, so a read causing the arm to lock cannot be ruled out from the Python source alone.

The Pi communicates with the board using I2C bus 1 at address `0x15`; see
[SERVO_COMMUNICATION.md](SERVO_COMMUNICATION.md) for the register and angle
mapping details. The main repository application does not import this driver
or initialize GPIO.

## Angles and motion timing

Angles use the vendor Arm_Lib logical convention, extended linearly beyond its documented angle marks. Servo angles have no software angle limit, and there is no jog-step cap. `jog` reads that servo's current angle and adds the requested delta; it cannot start if feedback is unavailable. `set` sends an absolute angle without requiring a reading first.

`pose`, `goto` and `goto2` send the five targets in ID order 1, 2, 3, 4, 5. Each uses the same movement duration, with a 10 ms pause between writes. This creates about a 40 ms start offset from the first to fifth command, plus I2C transfer time. For manual movement `MS` is 0..65535; 0 asks the board to use its fastest setting. `goto2` requires a nonzero duration to wait between route stages.

The driver converts each logical angle to the vendor's per-servo position code, then clamps the outgoing code to the extension-board range 96..4000. Read feedback is not angle-clamped and is converted back using the inverse mapping. ID 6 is rejected by the driver.

## Batch teaching and parking

On the Pi, use the release's teaching console with the existing point file:

```bash
python3 robot/arm_teaching/teach.py --state "$HOME/chess_arm/teach_points.json"
```

```text
coverage
teach approaches a2 a3 b3 park
torque on
goto park
```

Use ICCS coordinates with the red home rank at 0 and the black home rank at 9.
The teaching prompt asks you to support the arm and type `OFF` before releasing
torque. Press Enter at each pose to read and save the five angles. For a square,
record the grab/place pose only. Incomplete feedback can be retried, and previously saved
poses require explicit overwrite confirmation. Type `q` to stop teaching.
Support the arm before requesting `torque on`.

| Approach name | Files | Ranks |
| --- | --- | --- |
| `approach_red_left` | a–e | 0–4 |
| `approach_red_right` | f–i | 0–4 |
| `approach_black_left` | a–e | 5–9 |
| `approach_black_right` | f–i | 5–9 |

Teach an individual region with its name, or use `teach approaches` for all
four. As soon as one shared pose is saved, transfers and coverage use the
shared regions. A missing region blocks transfers involving that region.
Existing `<square>g` entries are reused. A file with zero shared poses still
supports the original per-square `<square>a` routes. Check clearances from
the shared pose to each grab/place pose before automatic movement.

Record `park` with the arm and tool clear of the board, all four markers and the
camera view. Check the route from the destination approach pose to `park`
before using automatic play. With `park` present, `goto2` releases the piece,
rises to the destination approach pose and then moves to `park`.

## Integrated one-move trial

Start the Web vision server and run from the deployment directory:

```bash
export PYTHONPATH="$PWD/.runtime-deps:$HOME/chch-vision-deps"
python3 -u tools/vision/one_move_arm_trial.py --arm-dir "$HOME/chess_arm" --check
python3 -u tools/vision/one_move_arm_trial.py --arm-dir "$HOME/chess_arm" --seconds 240
```

Start with the standard opening and the arm already parked. After the opening
is confirmed, play `c3c4` and withdraw your hand from the arm's reach. The runner
uses three matching ArUco frames, checks the move with the shared rules,
chooses a reachable non-capture reply, runs the teaching controller, and
verifies the full expected board after parking. With several reachable replies
it uses coordinate order. A missing `park` blocks the trial before hardware
initialization. The teaching console and runner share an exclusive arm lock.
The runner exits after one reply, or on timeout or any hardware error.

## Offline tests

From the repository root:

```sh
python3 -m unittest discover -s robot/arm_teaching/tests -v
```

Automated tests use fake I2C and GPIO objects and do not move hardware. The owner has tested piece transfers and reports smoother motion at 1000 ms. This synchronization was verified offline; torque/status behavior and physical arrival are not established by these tests.
