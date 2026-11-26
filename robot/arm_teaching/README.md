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
magnet pickup
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
c2a -> magnet on -> c2g -> wait 1 second -> c2a -> g5a -> g5g
    -> magnet off -> g5a
```

The suffix `a` means approach and `g` means grab/place. With both approach
layers recorded, the arm passes through the region pose before the square
pose on entry and retraces them on exit. If a movement fails or the macro is
interrupted after pickup, it
commands the magnet OFF before returning control. That can release the piece
where it is, so inspect the arm and board before continuing.

Point names use lowercase board coordinates: columns `a` through `i`, rows
`0` through `9`, plus `a`/`g`. Save the four example poses with:

```text
teach approaches c2a c2g g5a g5g
```

The existing manual commands remain available for calibration and
troubleshooting. The default for `set`, `jog`, `pose` and `goto` is 1000 ms.
For `goto2`, the duration of each segment uses the largest joint-angle change
at a nominal limit of 70 degrees per second, with a 100 ms minimum. It reads
the current joint angles before starting. An explicit `[MS]` sets a fixed
time. Each segment adds 150 ms to settle. The magnet turns on at the source approach, holds through transport, and turns off at the placement pose. These are timed waits, not arrival feedback.
Approach-to-approach moves follow joint motion; this tool does not plan
Cartesian paths or intermediate clearance waypoints.

Use `delete NAME` to remove a point saved under the wrong name or with an
incorrect pose. `del NAME` is a shorter alias. The change is written to
`teach_points.json` immediately; deleting an unknown name reports an error and
leaves the point file unchanged. Saving the same name again still replaces
that point with the current five-servo readings.

The electromagnet driver uses BCM GPIO20 (physical pin 38) as active-high enable and BCM GPIO21 (physical pin 40) as direction. Enable starts LOW and is driven LOW before each direction change and at placement. Use `magnet pickup` and `magnet release` to hold the selected direction until `magnet off` or console exit. GPIO21 LOW selects pickup and HIGH selects manual reverse direction. This requires `RPi.GPIO` on the Raspberry Pi.

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
teach a2a a3a b3a
teach a2g a3g b3g park
torque on
goto park
```

Use ICCS coordinates with the red home rank at 0 and the black home rank at 9.
The teaching prompt asks you to support the arm and type `OFF` before releasing
torque. Press Enter at each pose to read and save the five angles. Use the `a`
suffix for approach and `g` for grab/place. Incomplete feedback can be retried, and previously saved
poses require explicit overwrite confirmation. Type `q` to stop teaching.
Support the arm before requesting `torque on`.

| Approach name | Files | Ranks |
| --- | --- | --- |
| `approach_red_left` | a–e | 0–4 |
| `approach_red_right` | f–i | 0–4 |
| `approach_black_left` | a–e | 5–9 |
| `approach_black_right` | f–i | 5–9 |

Full-game play requires the four region poses and all 90 individual
approaches and grab/place poses. Existing region poses and `<square>g` entries
are reused. Manual transfers and the one-move trial also support older point
files with only one approach layer.
Use `coverage` to find missing individual poses.

Record `park` with the arm and tool clear of the board, all four markers and the
camera view. Check the route from the destination approach pose to `park`
before using automatic play. With `park` present, `goto2` releases the piece,
rises through the destination's approach path and then moves to `park`.

## Integrated one-move trial

### Game-over celebration

Use `teach celebration` to record `celebration1` and `celebration2` in one
session. Keep both poses and the path between them clear of the board.
With torque enabled and the arm at `park`, use `celebrate` to preview the
three A/B cycles. The magnet stays disabled and the arm returns to `park`.
The full-game runner plays the celebration after checkmate or stalemate.
It skips the celebration if either pose is missing.

### Capture storage for full-game trials

Run `teach captures` to record all 16 off-board slots in order, or use a
batch such as `teach capture1 capture2`. Each slot has an approach and release
pose, e.g. `capture1a` and `capture1g`. Use `coverage` to check the entries.
After enabling torque, test with `goto2 h0 capture1`.

The full-game runner requires all 32 poses. On each black capture the arm
uses the next slot in order and parks. After vision confirms the board
target is clear, it moves the black piece to that square and parks again.
Clear the capture area before starting a new game. For `--resume-moves` the
next slot is calculated from the black captures in the supplied history. Use
`--capture-start N` when the physical next free slot differs from the default.

### Full-game vision and optional Web preview

The full-game runner starts and stops its local camera and vision endpoint in
the same process. Run it on the Pi from the deployment directory:

```bash
export PYTHONPATH="$PWD/.runtime-deps:$HOME/chch-vision-deps"
CHRO_ARUCO_ALLOW_APPROXIMATE=1 python3 -u tools/vision/full_game_arm_trial.py \
  --engine "$HOME/pikafish/2025-06-23/pikafish" --execute
```

Choose `1` for the standard opening or `2` to use the current board. For an
endgame, choose the side to move. The runner reads the current layout from
three matching ArUco observations, then the human plays red and the arm
plays black. Use `--mode endgame --turn red` to supply those choices in the
command. The game ends with a winner announcement when the side to move is
checkmated or stalemated.

The full-game runner starts a Web preview on port 5000 and shuts it down when
the game exits. The Web UI forwards observations to the local vision endpoint
on port 5001. Service logs are written to `logs/vision.log` and `logs/web.log`
under the arm state directory (by default `~/chess_arm`).

To run the Web UI separately for diagnostics:

```bash
PYTHONPATH="$PWD/.runtime-deps:$HOME/chch-vision-deps" \
  CHRO_SKIP_STARTUP_PROMPT=1 CHRO_VISION_SERVICE_URL=http://127.0.0.1:5001 \
  python3 -u web_simulation/app.py
```

The one-move trial defaults to the legacy Web endpoint. Run it separately
from the deployment directory:

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
