"""Run a random, full-board batch, or user-specified closed transfer route."""

import argparse
from pathlib import Path
import random
import secrets
import sys
from typing import List, Optional

from .teach import (
    Electromagnet, PointStore, TeachController, TeachError, arm_lock, square_approach_path,
)

BOARD_SQUARES = tuple(f"{col}{rank}" for col in "abcdefghi" for rank in range(10))
BATCH_SIZE = 10
BATCH_COUNT = len(BOARD_SQUARES) // BATCH_SIZE
COVERAGE_SEED = 42

def shuffled_route(seed: int, start: str = "d5") -> List[str]:
    candidates = [square for square in BOARD_SQUARES if square != start]
    stops = random.Random(seed).sample(candidates, BATCH_SIZE - 1)
    return [start, *stops, start]


def coverage_routes(seed: int) -> List[List[str]]:
    squares = list(BOARD_SQUARES)
    random.Random(seed).shuffle(squares)
    return [squares[i:i + BATCH_SIZE] + [squares[i]]
            for i in range(0, len(squares), BATCH_SIZE)]


def check_route(route: List[str], store: PointStore) -> None:
    required = {"park"}
    for square in route:
        required.update((*square_approach_path(square, store.points), square + "g"))
    missing = sorted(required.difference(store.points))
    if missing:
        raise TeachError("Missing poses: " + ", ".join(missing))


def execute_route(controller: TeachController, route: List[str], move_time: Optional[int]) -> None:
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--batch", type=int, choices=range(BATCH_COUNT + 1),
                        help="0 previews the entire plan; 1–9 select one batch (default seed 42)")
    mode.add_argument("--route", nargs="+", metavar="SQUARE",
                      help="Visit these squares in order and return to the first")
    parser.add_argument("--ms", type=int, help="Override the default joint speed with a fixed waypoint time")
    parser.add_argument("--execute", action="store_true", help="Run the physical transfers")
    args = parser.parse_args()
    if args.ms is not None and not 1 <= args.ms <= 65535:
        parser.error("--ms must be between 1 and 65535")

    if args.batch == 0 and args.execute:
        parser.error("--execute requires a single batch (1–9) when using --batch")
    if args.route is not None:
        stops = [square.lower() for square in args.route]
        if len(stops) < 2 or len(stops) != len(set(stops)) or any(
                square not in BOARD_SQUARES for square in stops):
            parser.error("--route requires at least two distinct squares from a0 to i9")
        route = stops + [stops[0]]
        label = "Custom route"
    elif args.batch is not None:
        seed = args.seed if args.seed is not None else COVERAGE_SEED
        routes = coverage_routes(seed)
        if args.batch == 0:
            check_route(list(BOARD_SQUARES), PointStore(args.state))
            for index, route in enumerate(routes, 1):
                print(f"Seed {seed}, batch {index}: " + " -> ".join(route))
            print("All 90 squares and park are registered. Preview only.")
            return 0
        route = routes[args.batch - 1]
        label = f"Seed {seed}, batch {args.batch}"
    else:
        seed = args.seed if args.seed is not None else secrets.randbits(32)
        route = shuffled_route(seed)
        label = f"Seed {seed}"
    print(f"{label}: " + " -> ".join(route), flush=True)
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
