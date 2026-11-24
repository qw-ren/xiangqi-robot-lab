"""Move one piece through nine random a–g file stops and back to its start."""

import argparse
from pathlib import Path
import random
import secrets
import sys
from typing import List

from .teach import (
    Electromagnet, PointStore, TeachController, TeachError, arm_lock, duration, square_approach,
)


def shuffled_route(seed: int, start: str = "d5") -> List[str]:
    candidates = [f"{col}{rank}" for col in "abcdefg" for rank in range(10)
                  if f"{col}{rank}" != start]
    stops = random.Random(seed).sample(candidates, 9)
    return [start, *stops, start]


def check_route(route: List[str], store: PointStore) -> None:
    required = {"park"}
    for square in route:
        required.update((square + "g", square_approach(square, store.points)))
    missing = sorted(required.difference(store.points))
    if missing:
        raise TeachError("Missing poses: " + ", ".join(missing))


def execute_route(controller: TeachController, route: List[str], move_time: int) -> None:
    for step, (source, destination) in enumerate(zip(route, route[1:]), 1):
        print(f"Leg {step}/{len(route) - 1}: {source} -> {destination} -> park", flush=True)
        try:
            controller.transfer(source, destination, move_time, return_to_park=True)
        except Exception as exc:
            raise TeachError(f"Stopped at {source} -> {destination}; inspect the piece and arm: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path.home() / "chess_arm" / "teach_points.json")
    parser.add_argument("--seed", type=int, help="Reproduce a previously displayed route")
    parser.add_argument("--ms", type=int, default=duration(), help="Duration per waypoint in milliseconds")
    parser.add_argument("--execute", action="store_true", help="Run the physical transfers")
    args = parser.parse_args()
    if not 1 <= args.ms <= 65535:
        parser.error("--ms must be between 1 and 65535")

    seed = args.seed if args.seed is not None else secrets.randbits(32)
    route = shuffled_route(seed)
    print(f"Seed {seed}: " + " -> ".join(route), flush=True)
    with arm_lock():
        store = PointStore(args.state)
        check_route(route, store)
        if not args.execute:
            print("All required poses are registered. Preview only.")
            return 0
        from .Arm_Lib import Arm_Device

        arm = Arm_Device()
        magnet = Electromagnet()
        try:
            execute_route(TeachController(arm, store, electromagnet=magnet), route, args.ms)
        finally:
            magnet.close()
    print(f"Cycle complete: piece returned to {route[0]}; arm returned to park.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TeachError as exc:
        sys.exit(str(exc))
